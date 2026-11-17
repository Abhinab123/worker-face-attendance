from datetime import timedelta

import sqlalchemy as sa
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..forms import EmptyForm, LoginForm
from ..models import Admin
from ..security import safe_next_url
from ..services import audit
from ..timeutil import now_utc

bp = Blueprint("auth", __name__)

GENERIC_ERROR = "Invalid username or password."


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        cfg = current_app.config
        now = now_utc()
        admin = db.session.scalar(sa.select(Admin).where(Admin.username == form.username.data.strip()))
        if admin and admin.locked_until and admin.locked_until > now:
            flash("Too many failed attempts. This account is temporarily locked; try again later.", "danger")
            return render_template("auth/login.html", form=form), 429
        if admin and admin.is_enabled and admin.check_password(form.password.data):
            admin.failed_login_count, admin.locked_until, admin.last_login_at = 0, None, now
            audit.record("auth.login", admin_id=admin.id, entity_type="admin", entity_id=admin.id)
            db.session.commit()
            session.clear()  # drop any pre-login state (session fixation)
            login_user(admin)
            return redirect(safe_next_url(request.args.get("next")) or url_for("main.dashboard"))
        if admin:
            admin.failed_login_count += 1
            if admin.failed_login_count >= cfg["LOGIN_MAX_ATTEMPTS"]:
                admin.locked_until = now + timedelta(minutes=cfg["LOGIN_LOCKOUT_MINUTES"])
                admin.failed_login_count = 0
                audit.record("auth.lockout", actor_type="system", entity_type="admin", entity_id=admin.id)
            db.session.commit()
        flash(GENERIC_ERROR, "danger")
        return render_template("auth/login.html", form=form), 401
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    if EmptyForm().validate_on_submit():
        audit.record("auth.logout", entity_type="admin", entity_id=current_user.id)
        db.session.commit()
        logout_user()
        session.clear()
        flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
