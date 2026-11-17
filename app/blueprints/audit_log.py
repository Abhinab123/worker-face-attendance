from datetime import date, datetime, time, timedelta

import sqlalchemy as sa
from flask import Blueprint, render_template, request
from flask_login import login_required

from ..extensions import db
from ..models import AuditEvent, Worker
from ..timeutil import local_to_utc
from ._helpers import paginate

bp = Blueprint("audit_log", __name__, url_prefix="/audit")


@bp.before_request
@login_required
def _require_admin():
    pass


@bp.route("/")
def list_events():
    args = request.args
    stmt = sa.select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    action_prefix = (args.get("action") or "").strip()
    if action_prefix:
        stmt = stmt.where(AuditEvent.action.startswith(action_prefix, autoescape=True))
    worker_code = (args.get("worker") or "").strip().upper()
    if worker_code:
        stmt = stmt.join(Worker, Worker.id == AuditEvent.worker_id).where(Worker.worker_code == worker_code)
    for key, op in (("date_from", "ge"), ("date_to", "lt")):
        raw = (args.get(key) or "").strip()
        if raw:
            try:
                d = date.fromisoformat(raw)
            except ValueError:
                continue
            if op == "ge":
                stmt = stmt.where(AuditEvent.created_at >= local_to_utc(datetime.combine(d, time.min)))
            else:
                stmt = stmt.where(AuditEvent.created_at < local_to_utc(datetime.combine(d + timedelta(days=1), time.min)))
    actions = list(db.session.scalars(sa.select(AuditEvent.action).distinct().order_by(AuditEvent.action)))
    return render_template("audit/list.html", page=paginate(stmt, per_page=50), actions=actions)
