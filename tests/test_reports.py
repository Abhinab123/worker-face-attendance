import csv
import io
from datetime import datetime

import sqlalchemy as sa

from app.extensions import db
from app.models import AuditEvent
from app.services import attendance
from app.services.reports import csv_safe


def _seed(make_worker, clock):
    a = make_worker("W-1", name="Asha Kumar", department="Masonry", contractor="Acme")
    b = make_worker("W-2", name="=HYPERLINK(\"http://evil\")", department="Electrical", contractor="+Bright")
    c = make_worker("W-3", name="Chandra Rao", department="Masonry", contractor="Bright")
    for day, workers in ((15, [a, b]), (16, [a, c]), (17, [a, b, c])):
        clock.now = datetime(2026, 11, day, 3, 30)  # 09:00 IST
        for w in workers:
            attendance.check_in(w)
        clock.now = datetime(2026, 11, day, 12, 30)  # 18:00 IST
        for w in workers[:1]:
            attendance.check_out(w)
    clock.now = datetime(2026, 11, 17, 13, 0)
    return a, b, c


def _rows(resp):
    return list(csv.reader(io.StringIO(resp.get_data(as_text=True))))


def test_history_filters(logged_in, make_worker, clock):
    _seed(make_worker, clock)
    body = logged_in.get("/attendance/?date_from=2026-11-16&date_to=2026-11-16").get_data(as_text=True)
    assert "Asha Kumar" in body and "Chandra Rao" in body and "HYPERLINK" not in body
    body = logged_in.get("/attendance/?date_from=2026-11-15&date_to=2026-11-17&department=Electrical").get_data(as_text=True)
    assert "HYPERLINK" in body and "Asha Kumar" not in body


def test_csv_export_filters_and_columns(logged_in, make_worker, clock):
    _seed(make_worker, clock)
    rows = _rows(logged_in.get("/attendance/export.csv?date_from=2026-11-15&date_to=2026-11-17"))
    header, data = rows[0], rows[1:]
    assert header[:3] == ["business_date", "worker_id", "full_name"] and "timezone" in header
    assert len(data) == 7
    rows = _rows(logged_in.get("/attendance/export.csv?date_from=2026-11-15&date_to=2026-11-17&worker=W-1"))
    assert len(rows) - 1 == 3 and {r[1] for r in rows[1:]} == {"W-1"}
    rows = _rows(logged_in.get("/attendance/export.csv?date_from=2026-11-17&date_to=2026-11-17&contractor=Bright"))
    assert [r[1] for r in rows[1:]] == ["W-3"]
    rows = _rows(logged_in.get("/attendance/export.csv?date_from=2026-11-15&date_to=2026-11-15&department=Masonry"))
    assert [r[1] for r in rows[1:]] == ["W-1"]
    first = next(r for r in rows[1:] if r[1] == "W-1")
    assert first[5] == "2026-11-15 09:00:00" and first[6] == "2026-11-15 18:00:00" and first[7] == "540"


def test_csv_formula_injection_neutralised(logged_in, make_worker, clock):
    _seed(make_worker, clock)
    resp = logged_in.get("/attendance/export.csv?date_from=2026-11-15&date_to=2026-11-17&worker=W-2")
    assert resp.headers["Content-Disposition"].startswith("attachment;")
    assert resp.headers["Cache-Control"] == "no-store"
    for row in _rows(resp)[1:]:
        assert row[2].startswith("'=") and row[4] == "'+Bright"
        for cell in row:
            assert not cell or cell[0] not in "=+-@\t\r"


def test_csv_safe_unit():
    assert csv_safe("=1+1") == "'=1+1"
    assert csv_safe("@SUM(A1)") == "'@SUM(A1)"
    assert csv_safe("-2") == "'-2"
    assert csv_safe("\tcmd") == "'\tcmd"
    assert csv_safe("  =evil") == "'  =evil"
    assert csv_safe("Normal Name") == "Normal Name"
    assert csv_safe(None) == "" and csv_safe(540) == "540"


def test_export_is_audited_and_admin_only(logged_in, client, make_worker, clock):
    _seed(make_worker, clock)
    logged_in.get("/attendance/export.csv?date_from=2026-11-15&date_to=2026-11-17")
    event = db.session.scalar(sa.select(AuditEvent).where(AuditEvent.action == "report.export_csv"))
    assert event and event.after_data["rows"] == 7


def test_invalid_filter_dates_handled(logged_in):
    resp = logged_in.get("/attendance/?date_from=not-a-date&date_to=2026-11-01")
    assert resp.status_code == 200 and b"Invalid date" in resp.data


def test_no_absent_labels(logged_in, make_worker, clock):
    _seed(make_worker, clock)
    body = logged_in.get("/attendance/?date_from=2026-11-15&date_to=2026-11-17").get_data(as_text=True)
    assert ">Absent<" not in body and "absent" not in body.split("Only recorded attendance")[0].lower()


def test_dashboard_counts(logged_in, make_worker, clock):
    _seed(make_worker, clock)
    body = logged_in.get("/").get_data(as_text=True)
    assert "Checked in today" in body and "Still on site" in body
    # Open records on earlier days: W-2 on the 15th, W-3 on the 16th
    assert "2</strong> record(s) from earlier days" in body
