"""Audit trail. Events are added to the caller's transaction so they commit atomically
with the change they describe. Never pass images, encodings or secrets here."""
from __future__ import annotations

from flask import has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models import AuditEvent
from ..timeutil import now_utc


def record(action: str, *, actor_type: str | None = None, admin_id: int | None = None,
           kiosk_device_id: int | None = None, entity_type: str | None = None,
           entity_id: object = None, worker_id: int | None = None, reason: str | None = None,
           before: dict | None = None, after: dict | None = None) -> AuditEvent:
    if admin_id is None and has_request_context() and getattr(current_user, "is_authenticated", False):
        admin_id = current_user.id
    if actor_type is None:
        actor_type = "admin" if admin_id is not None else "system"
    event = AuditEvent(
        created_at=now_utc(),
        actor_type=actor_type,
        actor_admin_id=admin_id,
        kiosk_device_id=kiosk_device_id,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        worker_id=worker_id,
        reason=reason,
        before_data=before,
        after_data=after,
        ip_address=request.remote_addr if has_request_context() else None,
    )
    db.session.add(event)
    return event
