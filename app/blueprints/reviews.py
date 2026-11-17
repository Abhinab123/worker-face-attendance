import sqlalchemy as sa
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..forms import ResolveFlagForm
from ..models import FLAG_PENDING, EnrollmentFlag
from ..services import enrollment as enrollment_service
from ._helpers import paginate

bp = Blueprint("reviews", __name__, url_prefix="/reviews")


@bp.before_request
@login_required
def _require_admin():
    pass


@bp.route("/")
def list_flags():
    show = request.args.get("show", "pending")
    stmt = sa.select(EnrollmentFlag).order_by(EnrollmentFlag.created_at.desc())
    if show != "all":
        stmt = stmt.where(EnrollmentFlag.status == FLAG_PENDING)
    return render_template("reviews/list.html", page=paginate(stmt, per_page=25), show=show, form=ResolveFlagForm())


@bp.route("/<int:flag_id>/resolve", methods=["POST"])
def resolve(flag_id: int):
    flag = db.session.get(EnrollmentFlag, flag_id) or abort(404)
    form = ResolveFlagForm()
    if not form.validate_on_submit() or form.decision.data not in {"approve", "reject"}:
        flash("A decision and a note of at least 5 characters are required.", "danger")
        return redirect(url_for("reviews.list_flags"))
    try:
        enrollment_service.resolve_flag(flag, approve=form.decision.data == "approve", note=form.note.data,
                                        admin_id=current_user.id)
    except enrollment_service.EnrollmentError as err:
        flash(err.message, "danger")
    else:
        flash("Review resolved: " + ("samples activated (confirmed different people)."
                                     if form.decision.data == "approve" else "pending samples deleted."), "success")
    return redirect(url_for("reviews.list_flags"))
