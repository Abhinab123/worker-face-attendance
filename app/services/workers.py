"""Worker registration, editing, deactivation and search."""
from __future__ import annotations

import re
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import WORKER_ACTIVE, WORKER_INACTIVE, Worker
from ..timeutil import now_utc
from . import audit
from .recognition import invalidate_cache

WORKER_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,31}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 -]{6,18}[0-9]$")


class WorkerError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass
class WorkerInput:
    worker_code: str
    full_name: str
    department: str
    contractor: str
    phone: str | None = None


def normalize_code(code: str) -> str:
    return (code or "").strip().upper()


def validate(data: WorkerInput) -> WorkerInput:
    code = normalize_code(data.worker_code)
    if not WORKER_CODE_RE.match(code):
        raise WorkerError("Worker ID must be 2-32 characters: letters, digits, '-' or '_', starting with a letter or digit.", "worker_code")
    name = " ".join((data.full_name or "").split())
    if not 2 <= len(name) <= 120:
        raise WorkerError("Full name must be 2-120 characters.", "full_name")
    department = " ".join((data.department or "").split())
    if not 1 <= len(department) <= 80:
        raise WorkerError("Department is required (max 80 characters).", "department")
    contractor = " ".join((data.contractor or "").split())
    if not 1 <= len(contractor) <= 120:
        raise WorkerError("Contractor is required (max 120 characters).", "contractor")
    phone = (data.phone or "").strip() or None
    if phone and not PHONE_RE.match(phone):
        raise WorkerError("Phone number may contain digits, spaces, '-' and a leading '+' (8-20 characters).", "phone")
    return WorkerInput(code, name, department, contractor, phone)


def _snapshot(w: Worker) -> dict:
    return {"worker_code": w.worker_code, "full_name": w.full_name, "department": w.department,
            "contractor": w.contractor, "phone": w.phone, "status": w.status}


def create_worker(data: WorkerInput) -> Worker:
    clean = validate(data)
    if db.session.scalar(sa.select(Worker.id).where(Worker.worker_code == clean.worker_code)):
        raise WorkerError(f"Worker ID {clean.worker_code} already exists.", "worker_code")
    worker = Worker(worker_code=clean.worker_code, full_name=clean.full_name, department=clean.department,
                    contractor=clean.contractor, phone=clean.phone, status=WORKER_ACTIVE, created_at=now_utc())
    db.session.add(worker)
    try:
        db.session.flush()
    except IntegrityError:
        # Concurrent insert with the same code: the UNIQUE constraint is the final arbiter.
        db.session.rollback()
        raise WorkerError(f"Worker ID {clean.worker_code} already exists.", "worker_code") from None
    audit.record("worker.create", entity_type="worker", entity_id=worker.id, worker_id=worker.id, after=_snapshot(worker))
    db.session.commit()
    return worker


def update_worker(worker: Worker, data: WorkerInput) -> Worker:
    clean = validate(data)
    before = _snapshot(worker)
    if clean.worker_code != worker.worker_code:
        if db.session.scalar(sa.select(Worker.id).where(Worker.worker_code == clean.worker_code, Worker.id != worker.id)):
            raise WorkerError(f"Worker ID {clean.worker_code} already exists.", "worker_code")
    worker.worker_code, worker.full_name = clean.worker_code, clean.full_name
    worker.department, worker.contractor, worker.phone = clean.department, clean.contractor, clean.phone
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise WorkerError(f"Worker ID {clean.worker_code} already exists.", "worker_code") from None
    after = _snapshot(worker)
    if after != before:
        audit.record("worker.update", entity_type="worker", entity_id=worker.id, worker_id=worker.id, before=before, after=after)
    db.session.commit()
    return worker


def set_status(worker: Worker, active: bool, reason: str | None = None) -> Worker:
    target = WORKER_ACTIVE if active else WORKER_INACTIVE
    if worker.status == target:
        return worker
    before = _snapshot(worker)
    worker.status = target
    worker.deactivated_at = None if active else now_utc()
    audit.record("worker.reactivate" if active else "worker.deactivate", entity_type="worker", entity_id=worker.id,
                 worker_id=worker.id, reason=reason, before=before, after=_snapshot(worker))
    db.session.commit()
    invalidate_cache()  # inactive workers must stop matching immediately
    return worker


def search(q: str | None = None, department: str | None = None, contractor: str | None = None,
           status: str | None = None):
    stmt = sa.select(Worker)
    if q:
        q = q.strip()
        stmt = stmt.where(sa.or_(Worker.worker_code.contains(q.upper(), autoescape=True),
                                 Worker.full_name.contains(q, autoescape=True),
                                 Worker.phone.contains(q, autoescape=True)))
    if department:
        stmt = stmt.where(Worker.department == department)
    if contractor:
        stmt = stmt.where(Worker.contractor == contractor)
    if status in {WORKER_ACTIVE, WORKER_INACTIVE}:
        stmt = stmt.where(Worker.status == status)
    return stmt.order_by(Worker.worker_code)


def distinct_values(column) -> list[str]:
    return list(db.session.scalars(sa.select(column).distinct().order_by(column)))
