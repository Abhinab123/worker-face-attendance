"""Time handling.

All timestamps are stored as *naive UTC* ``DATETIME`` values. The business date of an
attendance record is the calendar date of the check-in in ``BUSINESS_TIMEZONE``.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (storage format)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_utc() -> datetime:
    """Clock used by the application; tests replace ``app.extensions['clock']``."""
    clock = current_app.extensions.get("clock", utcnow)
    return clock()


def business_tz() -> ZoneInfo:
    return ZoneInfo(current_app.config["BUSINESS_TIMEZONE"])


def to_local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(business_tz())


def business_date_for(value_utc: datetime) -> date:
    return to_local(value_utc).date()


def local_to_utc(local_naive: datetime) -> datetime:
    """Interpret a naive datetime as business-local time and return naive UTC."""
    aware = local_naive.replace(tzinfo=business_tz())
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def today_local() -> date:
    return business_date_for(now_utc())


def format_local(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    local = to_local(value)
    return local.strftime(fmt) if local else ""
