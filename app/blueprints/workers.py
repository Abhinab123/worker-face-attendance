import sqlalchemy as sa
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..face.imaging import ImageLimits, ImageRejected, decode_data_url
from ..forms import DeleteFaceDataForm, StatusForm, WorkerForm
from ..models import (ENCODING_ACTIVE, ENCODING_PENDING, AttendanceRecord, AuditEvent, EnrollmentFlag, FaceEncoding,
                      Worker)
from ..services import enrollment as enrollment_service
from ..services import workers as worker_service
from ._helpers import paginate

bp = Blueprint("workers", __name__, url_prefix="/workers")


@bp.before_request
@login_required
def _require_admin():
    """Every route in this blueprint is admin-only."""


def _get_worker(worker_id: int) -> Worker:
    worker = db.session.get(Worker, worker_id)
    if worker is None:
        abort(404)
    return worker


def _form_to_input(form: WorkerForm) -> worker_service.WorkerInput:
    return worker_service.WorkerInput(form.worker_code.data, form.full_name.data, form.department.data,
                                      form.contractor.data, form.phone.data)


@bp.route("/")
def list_workers():
    args = request.args
    stmt = worker_service.search(args.get("q"), args.get("department"), args.get("contractor"), args.get("status"))
    page = paginate(stmt, per_page=25)
    ids = [w.id for w in page.items]
    enrolled = dict(db.session.execute(
        sa.select(FaceEncoding.worker_id, sa.func.count(FaceEncoding.id))
        .where(FaceEncoding.worker_id.in_(ids), FaceEncoding.status == ENCODING_ACTIVE)
        .group_by(FaceEncoding.worker_id)).all()) if ids else {}
    return render_template("workers/list.html", page=page, enrolled=enrolled,
                           departments=worker_service.distinct_values(Worker.department),
                           contractors=worker_service.distinct_values(Worker.contractor))


@bp.route("/new", methods=["GET", "POST"])
def create():
    form = WorkerForm()
    if form.validate_on_submit():
        try:
            worker = worker_service.create_worker(_form_to_input(form))
        except worker_service.WorkerError as err:
            getattr(form, err.field or "worker_code").errors.append(err.message)
        else:
            flash(f"Worker {worker.worker_code} registered. Next: enroll their face.", "success")
            return redirect(url_for("workers.enroll", worker_id=worker.id))
    return render_template("workers/form.html", form=form, worker=None), 400 if form.errors else 200


@bp.route("/<int:worker_id>")
def detail(worker_id: int):
    worker = _get_worker(worker_id)
    encodings = db.session.execute(
        sa.select(FaceEncoding.status, sa.func.count(FaceEncoding.id), sa.func.max(FaceEncoding.created_at))
        .where(FaceEncoding.worker_id == worker.id).group_by(FaceEncoding.status)).all()
    enc = {status: {"count": n, "latest": latest} for status, n, latest in encodings}
    recent = db.session.scalars(sa.select(AttendanceRecord).where(AttendanceRecord.worker_id == worker.id)
                                .order_by(AttendanceRecord.business_date.desc()).limit(10)).all()
    history = db.session.scalars(sa.select(AuditEvent).where(AuditEvent.worker_id == worker.id)
                                 .order_by(AuditEvent.created_at.desc()).limit(15)).all()
    flags = db.session.scalars(sa.select(EnrollmentFlag).where(
        sa.or_(EnrollmentFlag.worker_id == worker.id, EnrollmentFlag.matched_worker_id == worker.id))
        .order_by(EnrollmentFlag.created_at.desc()).limit(10)).all()
    return render_template("workers/detail.html", worker=worker, enc=enc, recent=recent, history=history, flags=flags,
                           status_form=StatusForm(), delete_form=DeleteFaceDataForm(),
                           ENCODING_ACTIVE=ENCODING_ACTIVE, ENCODING_PENDING=ENCODING_PENDING)


@bp.route("/<int:worker_id>/edit", methods=["GET", "POST"])
def edit(worker_id: int):
    worker = _get_worker(worker_id)
    form = WorkerForm(obj=worker)
    if form.validate_on_submit():
        try:
            worker_service.update_worker(worker, _form_to_input(form))
        except worker_service.WorkerError as err:
            getattr(form, err.field or "worker_code").errors.append(err.message)
        else:
            flash("Worker details updated.", "success")
            return redirect(url_for("workers.detail", worker_id=worker.id))
    return render_template("workers/form.html", form=form, worker=worker), 400 if form.errors else 200


@bp.route("/<int:worker_id>/status", methods=["POST"])
def change_status(worker_id: int):
    worker = _get_worker(worker_id)
    form = StatusForm()
    if form.validate_on_submit() and form.action.data in {"activate", "deactivate"}:
        worker_service.set_status(worker, form.action.data == "activate", (form.reason.data or "").strip() or None)
        flash(f"Worker {worker.worker_code} {'reactivated' if worker.is_active_worker else 'deactivated'}. "
              "Attendance history is kept.", "success")
    else:
        flash("Could not change status.", "danger")
    return redirect(url_for("workers.detail", worker_id=worker.id))


@bp.route("/<int:worker_id>/face-data/delete", methods=["POST"])
def delete_face_data(worker_id: int):
    worker = _get_worker(worker_id)
    form = DeleteFaceDataForm()
    if form.validate_on_submit() and form.confirm.data:
        n = enrollment_service.delete_face_data(worker, reason=form.reason.data.strip())
        flash(f"Deleted {n} face samples for {worker.worker_code}. The worker can no longer be recognised "
              "until re-enrolled.", "success")
    else:
        flash("Deletion requires a reason and the confirmation checkbox.", "danger")
    return redirect(url_for("workers.detail", worker_id=worker.id))


@bp.route("/<int:worker_id>/enroll", methods=["GET"])
def enroll(worker_id: int):
    worker = _get_worker(worker_id)
    active = db.session.scalar(sa.select(sa.func.count(FaceEncoding.id)).where(
        FaceEncoding.worker_id == worker.id, FaceEncoding.status == ENCODING_ACTIVE)) or 0
    return render_template("workers/enroll.html", worker=worker, active_samples=active)


@bp.route("/<int:worker_id>/enroll", methods=["POST"])
def enroll_submit(worker_id: int):
    worker = _get_worker(worker_id)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("images"), list):
        return jsonify(status="rejected", message="Invalid request."), 400
    cfg = current_app.config
    if len(payload["images"]) > cfg["ENROLL_MAX_SAMPLES"]:
        return jsonify(status="rejected", message=f"At most {cfg['ENROLL_MAX_SAMPLES']} samples per submission."), 400
    limits = ImageLimits.from_config(cfg)
    raw_images = []
    for i, item in enumerate(payload["images"]):
        try:
            raw_images.append(decode_data_url(item, limits))
        except ImageRejected as rej:
            return jsonify(status="rejected", message=f"Sample {i + 1}: {rej.message}"), 400
    try:
        result = enrollment_service.enroll(worker, raw_images, consent_acknowledged=payload.get("consent") is True,
                                           admin_id=current_user.id)
    except enrollment_service.EnrollmentError as err:
        return jsonify(status="rejected", message=err.message), 400
    return jsonify(result.to_dict()), 200 if result.status != "rejected" else 422
