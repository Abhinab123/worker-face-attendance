"""Attendance history filters and CSV export.

Only *recorded* attendance is reported. Workers are never labelled "absent": that would need
an explicit roster/scheduling policy, which version 1 does not have.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta

import sqlalchemy as sa

from ..models import AttendanceRecord, Worker
from ..timeutil import format_local, today_local

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "|", "%")


def csv_safe(value) -> str:
    """Neutralise spreadsheet formula injection (CWE-1236): prefix risky cells with a quote."""
    text = "" if value is None else str(value)
    if text and (text[0] in FORMULA_PREFIXES or text.lstrip()[:1] in FORMULA_PREFIXES):
        return "'" + text
    return text


@dataclass
class ReportFilters:
    date_from: date
    date_to: date
    worker: str = ""
    department: str = ""
    contractor: str = ""
    corrected_only: bool = False

    MAX_RANGE_DAYS = 366

    @classmethod
    def from_args(cls, args) -> tuple["ReportFilters", list[str]]:
        errors: list[str] = []
        today = today_local()

        def parse(name, default):
            raw = (args.get(name) or "").strip()
            if not raw:
                return default
            try:
                return date.fromisoformat(raw)
            except ValueError:
                errors.append(f"Invalid date for {name.replace('_', ' ')}: use YYYY-MM-DD.")
                return default

        date_to = parse("date_to", today)
        date_from = parse("date_from", date_to - timedelta(days=6))
        if date_from > date_to:
            errors.append("'From' date is after 'To' date; the dates were swapped.")
            date_from, date_to = date_to, date_from
        if (date_to - date_from).days > cls.MAX_RANGE_DAYS:
            errors.append(f"Date range limited to {cls.MAX_RANGE_DAYS} days.")
            date_from = date_to - timedelta(days=cls.MAX_RANGE_DAYS)
        return cls(date_from, date_to, (args.get("worker") or "").strip()[:120],
                   (args.get("department") or "").strip()[:80], (args.get("contractor") or "").strip()[:120],
                   args.get("corrected_only") in {"1", "on", "true"}), errors

    def as_query_args(self) -> dict:
        out = {"date_from": self.date_from.isoformat(), "date_to": self.date_to.isoformat()}
        for key in ("worker", "department", "contractor"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.corrected_only:
            out["corrected_only"] = "1"
        return out


def attendance_query(f: ReportFilters):
    stmt = (sa.select(AttendanceRecord, Worker).join(Worker, Worker.id == AttendanceRecord.worker_id)
            .where(AttendanceRecord.business_date.between(f.date_from, f.date_to)))
    if f.worker:
        stmt = stmt.where(sa.or_(Worker.worker_code == f.worker.upper(),
                                 Worker.full_name.contains(f.worker, autoescape=True)))
    if f.department:
        stmt = stmt.where(Worker.department == f.department)
    if f.contractor:
        stmt = stmt.where(Worker.contractor == f.contractor)
    if f.corrected_only:
        stmt = stmt.where(AttendanceRecord.is_corrected.is_(True))
    return stmt.order_by(AttendanceRecord.business_date.desc(), Worker.worker_code)


CSV_HEADER = ["business_date", "worker_id", "full_name", "department", "contractor", "check_in_local",
              "check_out_local", "duration_minutes", "check_in_method", "check_out_method",
              "admin_corrected", "timezone"]


def iter_csv(rows, timezone_name: str):
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(CSV_HEADER)
    yield buffer.getvalue()
    for record, worker in rows:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow([csv_safe(v) for v in (
            record.business_date.isoformat(), worker.worker_code, worker.full_name, worker.department,
            worker.contractor, format_local(record.check_in_at), format_local(record.check_out_at),
            record.duration_minutes if record.duration_minutes is not None else "",
            record.check_in_method, record.check_out_method or "", "yes" if record.is_corrected else "no",
            timezone_name)])
        yield buffer.getvalue()
