import sqlalchemy as sa
from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..forms import CorrectionForm, ManualRecordForm
from ..models import AttendanceRecord, AuditEvent, Worker
from ..services import attendance as attendance_service
from ..services import audit
from ..services import workers as worker_service
from ..services.reports import ReportFilters, attendance_query, iter_csv
from ..timeutil import format_local, now_utc, to_local, today_local
from ._helpers import paginate

bp = Blueprint("attendance", __name__, url_prefix="/attendance")


@bp.before_request
@login_required
def _require_admin():
    pass


@bp.route("/")
def history():
    filters, errors = ReportFilters.from_args(request.args)
    for e in errors:
        flash(e, "warning")
    page = paginate(attendance_query(filters), per_page=50, scalars=False)
    return render_template("attendance/list.html", page=page, filters=filters,
                           departments=worker_service.distinct_values(Worker.department),
                           contractors=worker_service.distinct_values(Worker.contractor))


@bp.route("/export.csv")
def export_csv():
    filters, _errors = ReportFilters.from_args(request.args)
    rows = db.session.execute(attendance_query(filters)).all()
    audit.record("report.export_csv", entity_type="report", after={**filters.as_query_args(), "rows": len(rows)})
    db.session.commit()
    name = f"attendance_{filters.date_from:%Y%m%d}_{filters.date_to:%Y%m%d}.csv"
    return Response(iter_csv(rows, current_app.config["BUSINESS_TIMEZONE"]), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"',
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _report(form, err) -> None:
    if err.code == "reason_required":
        form.reason.errors.append(err.message)
    else:
        flash(err.message, "danger")


def _fmt(value):
    local = to_local(value)
    return local.replace(tzinfo=None, second=0, microsecond=0) if local else None


@bp.route("/<int:record_id>/correct", methods=["GET", "POST"])
def correct(record_id: int):
    record = db.session.get(AttendanceRecord, record_id) or abort(404)
    form = CorrectionForm()
    if request.method == "GET":
        form.check_in_at.data, form.check_out_at.data = _fmt(record.check_in_at), _fmt(record.check_out_at)
    if form.validate_on_submit():
        try:
            attendance_service.correct_record(record, check_in_local=form.check_in_at.data,
                                              check_out_local=form.check_out_at.data, reason=form.reason.data,
                                              admin_id=current_user.id)
        except attendance_service.AttendanceError as err:
            _report(form, err)
        else:
            flash("Record corrected. The change is recorded in the audit history.", "success")
            return redirect(url_for("attendance.history", date_from=record.business_date.isoformat(),
                                    date_to=record.business_date.isoformat()))
    history = db.session.scalars(sa.select(AuditEvent).where(AuditEvent.entity_type == "attendance",
                                                             AuditEvent.entity_id == str(record.id))
                                 .order_by(AuditEvent.created_at.desc())).all()
    return render_template("attendance/correct.html", form=form, record=record, history=history)


@bp.route("/new", methods=["GET", "POST"])
def manual_create():
    form = ManualRecordForm()
    if request.method == "GET" and request.args.get("worker"):
        form.worker_code.data = request.args["worker"]
    if form.validate_on_submit():
        worker = db.session.scalar(sa.select(Worker).where(
            Worker.worker_code == worker_service.normalize_code(form.worker_code.data)))
        if worker is None:
            form.worker_code.errors.append("No worker with this ID.")
        else:
            try:
                record = attendance_service.create_manual_record(
                    worker, check_in_local=form.check_in_at.data, check_out_local=form.check_out_at.data,
                    reason=form.reason.data, admin_id=current_user.id)
            except attendance_service.AttendanceError as err:
                _report(form, err)
            else:
                flash(f"Manual record created for {worker.worker_code} on {record.business_date}. "
                      "It is marked as an administrator entry.", "success")
                return redirect(url_for("attendance.history", date_from=record.business_date.isoformat(),
                                        date_to=record.business_date.isoformat()))
    return render_template("attendance/new.html", form=form, today=today_local(),
                           now_local=format_local(now_utc(), "%Y-%m-%dT%H:%M"))
