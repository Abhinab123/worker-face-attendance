"""Attendance rules.

- One record per worker per business date: ``UNIQUE(worker_id, business_date)``. A check-in is a
  plain INSERT; when two requests race, the database rejects the second with an IntegrityError.
- Check-out is a single conditional ``UPDATE ... WHERE check_out_at IS NULL``; only one of several
  concurrent check-outs can change a row.
- The business date is the check-in's calendar date in ``BUSINESS_TIMEZONE``.
- Overnight shifts are NOT supported in version 1: check-out must happen on the same business
  date as the check-in. An open record from a previous date can only be closed by an admin correction.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import sqlalchemy as sa
from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import METHOD_ADMIN, METHOD_FACE, AttendanceRecord, Worker
from ..timeutil import business_date_for, format_local, local_to_utc, now_utc, to_local
from . import audit


class AttendanceError(Exception):
    def __init__(self, code: str, message: str, record: AttendanceRecord | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.record = record


def _record_for(worker_id: int, business_date: date) -> AttendanceRecord | None:
    return db.session.scalar(sa.select(AttendanceRecord).where(
        AttendanceRecord.worker_id == worker_id, AttendanceRecord.business_date == business_date))


def check_in(worker: Worker, *, method: str = METHOD_FACE, distance: float | None = None,
             device_id: int | None = None, admin_id: int | None = None, now: datetime | None = None) -> AttendanceRecord:
    now = now or now_utc()
    if not worker.is_active_worker:
        raise AttendanceError("inactive_worker", "This worker is inactive and cannot record attendance.")
    business_date = business_date_for(now)
    record = AttendanceRecord(worker_id=worker.id, business_date=business_date, check_in_at=now,
                              check_in_method=method, check_in_distance=distance, check_in_device_id=device_id,
                              created_at=now, updated_at=now)
    db.session.add(record)
    try:
        db.session.flush()
        audit.record("attendance.check_in", actor_type="kiosk" if method == METHOD_FACE else "admin",
                     admin_id=admin_id, kiosk_device_id=device_id, entity_type="attendance", entity_id=record.id,
                     worker_id=worker.id, after=record.snapshot())
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = _record_for(worker.id, business_date)
        if existing and existing.check_out_at:
            raise AttendanceError("already_completed", f"Attendance for {business_date:%d %b %Y} is already complete "
                                  f"(checked out at {format_local(existing.check_out_at, '%H:%M')}).", existing) from None
        when = format_local(existing.check_in_at, "%H:%M") if existing else "earlier"
        raise AttendanceError("already_checked_in", f"Already checked in today at {when}.", existing) from None
    return record


def check_out(worker: Worker, *, method: str = METHOD_FACE, distance: float | None = None,
              device_id: int | None = None, admin_id: int | None = None, now: datetime | None = None) -> AttendanceRecord:
    now = now or now_utc()
    if not worker.is_active_worker:
        raise AttendanceError("inactive_worker", "This worker is inactive and cannot record attendance.")
    business_date = business_date_for(now)
    result = db.session.execute(
        sa.update(AttendanceRecord)
        .where(AttendanceRecord.worker_id == worker.id, AttendanceRecord.business_date == business_date,
               AttendanceRecord.check_out_at.is_(None), AttendanceRecord.check_in_at <= now)
        .values(check_out_at=now, check_out_method=method, check_out_distance=distance,
                check_out_device_id=device_id, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 1:
        record = _record_for(worker.id, business_date)
        db.session.refresh(record)
        audit.record("attendance.check_out", actor_type="kiosk" if method == METHOD_FACE else "admin",
                     admin_id=admin_id, kiosk_device_id=device_id, entity_type="attendance", entity_id=record.id,
                     worker_id=worker.id, after=record.snapshot())
        db.session.commit()
        return record

    db.session.rollback()
    existing = _record_for(worker.id, business_date)
    if existing is None:
        previous_open = db.session.scalar(
            sa.select(AttendanceRecord).where(AttendanceRecord.worker_id == worker.id,
                                              AttendanceRecord.business_date < business_date,
                                              AttendanceRecord.check_out_at.is_(None))
            .order_by(AttendanceRecord.business_date.desc()).limit(1))
        if previous_open is not None:
            raise AttendanceError(
                "overnight_unsupported",
                f"No check-in today. The open check-in from {previous_open.business_date:%d %b %Y} cannot be closed "
                "after midnight: overnight shifts are not supported. Ask an administrator to correct it.",
                previous_open)
        raise AttendanceError("not_checked_in", "You have not checked in today. Check in first.")
    if existing.check_out_at is not None:
        raise AttendanceError("already_checked_out",
                              f"Already checked out today at {format_local(existing.check_out_at, '%H:%M')}.", existing)
    raise AttendanceError("invalid_time", "Check-out time is before the check-in time.", existing)


# ---------------------------------------------------------------- admin corrections

def _validate_times(business_date: date, check_in_local: datetime, check_out_local: datetime | None) -> tuple[datetime, datetime | None]:
    if check_in_local.date() != business_date:
        raise AttendanceError("invalid_time", "Check-in time must fall on the record's business date.")
    if check_out_local is not None:
        if check_out_local < check_in_local:
            raise AttendanceError("invalid_time", "Check-out cannot be earlier than check-in.")
        if check_out_local.date() != business_date:
            raise AttendanceError("overnight_unsupported",
                                  "Check-out must be on the same date as check-in (overnight shifts are not supported).")
    in_utc = local_to_utc(check_in_local)
    out_utc = local_to_utc(check_out_local) if check_out_local else None
    horizon = now_utc() + timedelta(minutes=5)
    if in_utc > horizon or (out_utc and out_utc > horizon):
        raise AttendanceError("invalid_time", "Attendance times cannot be in the future.")
    return in_utc, out_utc


def _validate_reason(reason: str) -> str:
    reason = (reason or "").strip()
    minimum = current_app.config["CORRECTION_REASON_MIN_LENGTH"]
    if len(reason) < minimum:
        raise AttendanceError("reason_required", f"A reason of at least {minimum} characters is required for corrections.")
    return reason


def correct_record(record: AttendanceRecord, *, check_in_local: datetime, check_out_local: datetime | None,
                   reason: str, admin_id: int) -> AttendanceRecord:
    reason = _validate_reason(reason)
    in_utc, out_utc = _validate_times(record.business_date, check_in_local, check_out_local)
    before = record.snapshot()
    changed = False
    if in_utc != record.check_in_at:
        record.check_in_at, record.check_in_method, record.check_in_distance = in_utc, METHOD_ADMIN, None
        record.check_in_device_id = None
        changed = True
    if out_utc != record.check_out_at:
        record.check_out_at = out_utc
        record.check_out_method = METHOD_ADMIN if out_utc else None
        record.check_out_distance, record.check_out_device_id = None, None
        changed = True
    if not changed:
        raise AttendanceError("no_change", "No changes were made.")
    record.is_corrected = True
    record.updated_at = now_utc()
    audit.record("attendance.correct", actor_type="admin", admin_id=admin_id, entity_type="attendance",
                 entity_id=record.id, worker_id=record.worker_id, reason=reason, before=before, after=record.snapshot())
    db.session.commit()
    return record


def create_manual_record(worker: Worker, *, check_in_local: datetime, check_out_local: datetime | None,
                         reason: str, admin_id: int) -> AttendanceRecord:
    reason = _validate_reason(reason)
    business_date = check_in_local.date()
    in_utc, out_utc = _validate_times(business_date, check_in_local, check_out_local)
    now = now_utc()
    record = AttendanceRecord(worker_id=worker.id, business_date=business_date, check_in_at=in_utc,
                              check_out_at=out_utc, check_in_method=METHOD_ADMIN,
                              check_out_method=METHOD_ADMIN if out_utc else None, is_corrected=True,
                              created_at=now, updated_at=now)
    db.session.add(record)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise AttendanceError("duplicate", f"{worker.worker_code} already has a record for {business_date}; "
                              "edit that record instead.") from None
    audit.record("attendance.manual_create", actor_type="admin", admin_id=admin_id, entity_type="attendance",
                 entity_id=record.id, worker_id=worker.id, reason=reason, before=None, after=record.snapshot())
    db.session.commit()
    return record


def local_time_on(business_date: date, value: datetime | None) -> str:
    local = to_local(value)
    return local.strftime("%H:%M") if local else ""
