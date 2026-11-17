from datetime import date, datetime

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import AttendanceRecord, AuditEvent
from app.services import attendance
from app.services.attendance import AttendanceError
from app.services.workers import set_status
from app.timeutil import business_date_for, format_local


def test_check_in_and_out(app, make_worker, clock):
    w = make_worker()
    rec = attendance.check_in(w, distance=0.31)
    assert rec.business_date == date(2026, 11, 17) and rec.check_in_method == "face"
    clock.advance(hours=8)
    out = attendance.check_out(w, distance=0.33)
    assert out.check_out_at > out.check_in_at and out.duration_minutes == 480


def test_duplicate_check_in_rejected(app, make_worker, clock):
    w = make_worker()
    attendance.check_in(w)
    clock.advance(minutes=5)
    with pytest.raises(AttendanceError) as err:
        attendance.check_in(w)
    assert err.value.code == "already_checked_in" and "10:00" in err.value.message
    assert db.session.scalar(sa.select(sa.func.count(AttendanceRecord.id))) == 1


def test_check_in_after_completed_day_rejected(app, make_worker, clock):
    w = make_worker()
    attendance.check_in(w)
    clock.advance(hours=1)
    attendance.check_out(w)
    with pytest.raises(AttendanceError) as err:
        attendance.check_in(w)
    assert err.value.code == "already_completed"


def test_duplicate_check_out_rejected(app, make_worker, clock):
    w = make_worker()
    attendance.check_in(w)
    clock.advance(hours=1)
    attendance.check_out(w)
    clock.advance(hours=1)
    with pytest.raises(AttendanceError) as err:
        attendance.check_out(w)
    assert err.value.code == "already_checked_out"


def test_check_out_before_check_in_rejected(app, make_worker):
    w = make_worker()
    with pytest.raises(AttendanceError) as err:
        attendance.check_out(w)
    assert err.value.code == "not_checked_in"
    assert db.session.scalar(sa.select(sa.func.count(AttendanceRecord.id))) == 0


def test_inactive_worker_cannot_record(app, make_worker):
    w = make_worker()
    set_status(w, False)
    with pytest.raises(AttendanceError) as err:
        attendance.check_in(w)
    assert err.value.code == "inactive_worker"


def test_unique_constraint_enforced_by_database(app, make_worker, clock):
    w = make_worker()
    attendance.check_in(w)
    db.session.add(AttendanceRecord(worker_id=w.id, business_date=date(2026, 11, 17), check_in_at=clock.now,
                                    check_in_method="face"))
    with pytest.raises(sa.exc.IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_checkout_before_checkin_check_constraint(app, make_worker, clock):
    w = make_worker()
    db.session.add(AttendanceRecord(worker_id=w.id, business_date=date(2026, 11, 17), check_in_at=clock.now,
                                    check_out_at=datetime(2026, 11, 17, 1, 0), check_in_method="face"))
    with pytest.raises(sa.exc.IntegrityError):
        db.session.flush()
    db.session.rollback()


# ------------------------------------------------------------------ timezone / business date

def test_business_date_boundaries_asia_kolkata(app):
    # IST = UTC+05:30. 18:29:59 UTC is 23:59:59 IST; 18:30:00 UTC is 00:00 IST next day.
    assert business_date_for(datetime(2026, 1, 1, 18, 29, 59)) == date(2026, 1, 1)
    assert business_date_for(datetime(2026, 1, 1, 18, 30, 0)) == date(2026, 1, 2)
    assert format_local(datetime(2026, 1, 1, 18, 30, 0)) == "2026-01-02 00:00:00"


def test_configurable_timezone(app):
    app.config["BUSINESS_TIMEZONE"] = "America/New_York"
    assert business_date_for(datetime(2026, 1, 2, 3, 0)) == date(2026, 1, 1)  # 22:00 EST previous day


def test_check_ins_either_side_of_local_midnight(app, make_worker, clock):
    w = make_worker()
    clock.now = datetime(2026, 11, 17, 18, 29, 0)  # 23:59 IST on 17 Nov
    assert attendance.check_in(w).business_date == date(2026, 11, 17)
    clock.now = datetime(2026, 11, 17, 18, 31, 0)  # 00:01 IST on 18 Nov
    assert attendance.check_in(w).business_date == date(2026, 11, 18)


def test_overnight_check_out_rejected_clearly(app, make_worker, clock):
    w = make_worker()
    clock.now = datetime(2026, 11, 17, 16, 0)  # 21:30 IST
    attendance.check_in(w)
    clock.now = datetime(2026, 11, 17, 20, 0)  # 01:30 IST next day
    with pytest.raises(AttendanceError) as err:
        attendance.check_out(w)
    assert err.value.code == "overnight_unsupported" and "overnight" in err.value.message


def test_timestamps_stored_as_utc(app, make_worker, clock):
    w = make_worker()
    attendance.check_in(w)
    db.session.expire_all()
    stored = db.session.scalar(sa.select(AttendanceRecord.check_in_at))
    assert stored == clock.now  # naive UTC
    assert format_local(stored, "%H:%M") == "10:00"


# ------------------------------------------------------------------ admin corrections

def _record(make_worker, clock):
    w = make_worker()
    attendance.check_in(w, distance=0.3)
    return w, db.session.scalar(sa.select(AttendanceRecord))


def test_correction_requires_reason(logged_in, make_worker, clock):
    _w, rec = _record(make_worker, clock)
    logged_in.get(f"/attendance/{rec.id}/correct")
    resp = logged_in.post(f"/attendance/{rec.id}/correct",
                          {"check_in_at": "2026-11-17T09:00", "check_out_at": "", "reason": "short"})
    assert b"reason of at least" in resp.data
    db.session.expire_all()
    assert not db.session.get(AttendanceRecord, rec.id).is_corrected


def test_correction_audit_trail(logged_in, make_worker, clock, admin):
    clock.advance(hours=9)  # now 19:00 IST
    w = make_worker()
    clock.now = datetime(2026, 11, 17, 4, 30)
    attendance.check_in(w, distance=0.3)
    clock.now = datetime(2026, 11, 17, 13, 30)
    rec = db.session.scalar(sa.select(AttendanceRecord))
    logged_in.get(f"/attendance/{rec.id}/correct")
    resp = logged_in.post(f"/attendance/{rec.id}/correct", {
        "check_in_at": "2026-11-17T09:00", "check_out_at": "2026-11-17T17:30",
        "reason": "Worker forgot to check out; supervisor confirmed"})
    assert resp.status_code == 302
    db.session.expire_all()
    rec = db.session.get(AttendanceRecord, rec.id)
    assert rec.is_corrected and rec.check_in_method == "admin" and rec.check_out_method == "admin"
    assert format_local(rec.check_in_at, "%H:%M") == "09:00" and format_local(rec.check_out_at, "%H:%M") == "17:30"
    event = db.session.scalar(sa.select(AuditEvent).where(AuditEvent.action == "attendance.correct"))
    assert event.actor_admin_id == admin.id and event.reason.startswith("Worker forgot")
    assert event.before_data["check_in_method"] == "face" and event.before_data["check_out_at_utc"] is None
    assert event.after_data["check_in_method"] == "admin" and event.after_data["check_out_at_utc"] == "2026-11-17T12:00:00"
    assert event.created_at is not None
    page = logged_in.get(f"/attendance/{rec.id}/correct").get_data(as_text=True)
    assert "Worker forgot to check out" in page


def test_correction_rejects_overnight_and_future(app, make_worker, clock, admin):
    _w, rec = _record(make_worker, clock)
    with pytest.raises(AttendanceError) as err:
        attendance.correct_record(rec, check_in_local=datetime(2026, 11, 17, 9), check_out_local=datetime(2026, 11, 18, 1),
                                  reason="Night shift entry", admin_id=admin.id)
    assert err.value.code == "overnight_unsupported"
    with pytest.raises(AttendanceError) as err:
        attendance.correct_record(rec, check_in_local=datetime(2026, 11, 17, 9), check_out_local=datetime(2026, 11, 17, 23),
                                  reason="Future time entry", admin_id=admin.id)
    assert err.value.code == "invalid_time"
    with pytest.raises(AttendanceError):
        attendance.correct_record(rec, check_in_local=datetime(2026, 11, 17, 9), check_out_local=datetime(2026, 11, 17, 8),
                                  reason="Out before in entry", admin_id=admin.id)


def test_overnight_open_record_closed_by_admin(app, make_worker, clock, admin):
    w = make_worker()
    clock.now = datetime(2026, 11, 17, 14, 0)  # 19:30 IST
    attendance.check_in(w)
    clock.now = datetime(2026, 11, 18, 4, 30)
    rec = db.session.scalar(sa.select(AttendanceRecord))
    attendance.correct_record(rec, check_in_local=datetime(2026, 11, 17, 19, 30),
                              check_out_local=datetime(2026, 11, 17, 23, 59), reason="Closed per supervisor log",
                              admin_id=admin.id)
    assert rec.check_out_method == "admin"


def test_manual_record(logged_in, make_worker, admin):
    w = make_worker("W-9")
    logged_in.get("/attendance/new")
    resp = logged_in.post("/attendance/new", {"worker_code": "w-9", "check_in_at": "2026-11-16T08:00",
                                              "check_out_at": "2026-11-16T16:00", "reason": "Camera was broken"})
    assert resp.status_code == 302
    rec = db.session.scalar(sa.select(AttendanceRecord))
    assert rec.business_date == date(2026, 11, 16) and rec.check_in_method == "admin" and rec.is_corrected
    assert db.session.scalar(sa.select(AuditEvent).where(AuditEvent.action == "attendance.manual_create")).reason
    # A second manual record for the same date is rejected (unique constraint).
    logged_in.get("/attendance/new")
    resp = logged_in.post("/attendance/new", {"worker_code": "W-9", "check_in_at": "2026-11-16T09:00",
                                              "reason": "Duplicate attempt here"})
    assert b"already has a record" in resp.data
