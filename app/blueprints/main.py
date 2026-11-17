import sqlalchemy as sa
from flask import Blueprint, jsonify, render_template
from flask_login import login_required

from ..extensions import db
from ..models import (ENCODING_ACTIVE, FLAG_PENDING, WORKER_ACTIVE, AttendanceRecord, AuditEvent, EnrollmentFlag,
                      FaceEncoding, Worker)
from ..timeutil import today_local

bp = Blueprint("main", __name__)


@bp.route("/healthz")
def health():
    """Liveness/readiness probe. Deliberately reveals no versions, hosts or error details."""
    try:
        db.session.execute(sa.text("SELECT 1"))
        return jsonify(status="ok", database="ok")
    except Exception:
        db.session.rollback()
        return jsonify(status="degraded", database="unavailable"), 503


@bp.route("/")
@login_required
def dashboard():
    today = today_local()
    count = lambda stmt: db.session.scalar(stmt) or 0  # noqa: E731
    stats = {
        "active_workers": count(sa.select(sa.func.count(Worker.id)).where(Worker.status == WORKER_ACTIVE)),
        "enrolled_workers": count(
            sa.select(sa.func.count(sa.distinct(FaceEncoding.worker_id)))
            .join(Worker, Worker.id == FaceEncoding.worker_id)
            .where(Worker.status == WORKER_ACTIVE, FaceEncoding.status == ENCODING_ACTIVE)),
        "checked_in_today": count(sa.select(sa.func.count(AttendanceRecord.id))
                                  .where(AttendanceRecord.business_date == today)),
        "checked_out_today": count(sa.select(sa.func.count(AttendanceRecord.id))
                                   .where(AttendanceRecord.business_date == today,
                                          AttendanceRecord.check_out_at.is_not(None))),
        "missing_checkouts": count(sa.select(sa.func.count(AttendanceRecord.id))
                                   .where(AttendanceRecord.business_date < today,
                                          AttendanceRecord.check_out_at.is_(None))),
        "pending_reviews": count(sa.select(sa.func.count(EnrollmentFlag.id))
                                 .where(EnrollmentFlag.status == FLAG_PENDING)),
    }
    stats["on_site"] = stats["checked_in_today"] - stats["checked_out_today"]
    recent = db.session.scalars(sa.select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                                .limit(15)).all()
    return render_template("dashboard.html", stats=stats, recent=recent, today=today)
