"""Attendance kiosk.

Access requires either a signed-in administrator, or a browser explicitly enrolled as a kiosk
device by an administrator (one-time activation code; revocable). The camera runs in the
*browser*; frames are posted to the server for recognition.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

import sqlalchemy as sa
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..forms import ActivateForm, DeviceForm, EmptyForm
from ..models import MODE_CHECK_IN, MODE_CHECK_OUT, KioskDevice
from ..face.liveness import INSTRUCTIONS
from ..services import kiosk as kiosk_service
from ..timeutil import now_utc

bp = Blueprint("kiosk", __name__, url_prefix="/kiosk")

_SESSION_KEYS = ("kiosk_device_id", "kiosk_session_id")


def current_kiosk() -> kiosk_service.KioskContext | None:
    device_id = session.get("kiosk_device_id")
    if device_id:
        device = db.session.get(KioskDevice, device_id)
        if device is None or device.is_revoked:
            for key in _SESSION_KEYS:
                session.pop(key, None)
            return None
        now = now_utc()
        if device.last_seen_at is None or now - device.last_seen_at > timedelta(minutes=1):
            device.last_seen_at = now
            db.session.commit()
    elif not current_user.is_authenticated:
        return None
    if not session.get("kiosk_session_id"):
        session["kiosk_session_id"] = secrets.token_hex(16)
    admin_id = current_user.id if current_user.is_authenticated else None
    return kiosk_service.KioskContext(session["kiosk_session_id"], device_id, admin_id)


def _api_unauthorized():
    return jsonify(ok=False, status="kiosk_not_authorized", title="Kiosk not authorised",
                   message="This browser is not an authorised kiosk. Ask an administrator to activate it."), 401


@bp.route("/")
def kiosk_page():
    ctx = current_kiosk()
    if ctx is None:
        return redirect(url_for("kiosk.activate"))
    device = db.session.get(KioskDevice, ctx.device_id) if ctx.device_id else None
    cfg = current_app.config
    return render_template("kiosk/kiosk.html", device=device, exit_form=EmptyForm(),
                           frame_count=cfg["LIVENESS_FRAME_COUNT"], frame_interval=cfg["LIVENESS_FRAME_INTERVAL_MS"],
                           liveness_required=cfg["LIVENESS_REQUIRED"])


@bp.route("/activate", methods=["GET", "POST"])
def activate():
    form = ActivateForm()
    if form.validate_on_submit():
        device = kiosk_service.activate_device(form.token.data)
        if device is None:
            form.token.errors.append("Invalid or revoked activation code.")
        else:
            session.permanent = True
            session["kiosk_device_id"] = device.id
            session["kiosk_session_id"] = secrets.token_hex(16)
            flash(f"This browser is now the kiosk '{device.name}'.", "success")
            return redirect(url_for("kiosk.kiosk_page"))
    return render_template("kiosk/activate.html", form=form), 400 if form.errors else 200


@bp.route("/exit", methods=["POST"])
def exit_kiosk():
    if EmptyForm().validate_on_submit():
        for key in _SESSION_KEYS:
            session.pop(key, None)
    return redirect(url_for("main.dashboard") if current_user.is_authenticated else url_for("kiosk.activate"))


@bp.route("/api/challenge", methods=["POST"])
def api_challenge():
    ctx = current_kiosk()
    if ctx is None:
        return _api_unauthorized()
    payload = request.get_json(silent=True) or {}
    mode = payload.get("mode")
    if mode not in {MODE_CHECK_IN, MODE_CHECK_OUT}:
        return jsonify(ok=False, status="invalid_request", title="Choose a mode",
                       message="Choose Check in or Check out."), 400
    challenge, token = kiosk_service.issue_challenge(ctx, mode)
    cfg = current_app.config
    return jsonify(ok=True, token=token, challenge_type=challenge.challenge_type,
                   instruction=INSTRUCTIONS[challenge.challenge_type], mode=mode,
                   expires_in=cfg["LIVENESS_CHALLENGE_TTL_SECONDS"], frame_count=cfg["LIVENESS_FRAME_COUNT"],
                   frame_interval_ms=cfg["LIVENESS_FRAME_INTERVAL_MS"])


@bp.route("/api/verify", methods=["POST"])
def api_verify():
    ctx = current_kiosk()
    if ctx is None:
        return _api_unauthorized()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, status="invalid_request", title="Please try again", message="Invalid request."), 400
    outcome = kiosk_service.verify_attendance(ctx, payload.get("token"), payload.get("mode"), payload.get("frames"))
    return jsonify(outcome.to_dict()), 200


# ---------------------------------------------------------------- device administration

@bp.route("/devices", methods=["GET", "POST"])
@login_required
def devices():
    form = DeviceForm()
    new_token = None
    new_device = None
    if form.validate_on_submit():
        try:
            new_device, new_token = kiosk_service.create_device(form.name.data, current_user.id)
        except ValueError as err:
            form.name.errors.append(str(err))
        else:
            form = DeviceForm(formdata=None)
    rows = db.session.scalars(sa.select(KioskDevice).order_by(KioskDevice.created_at.desc())).all()
    return render_template("kiosk/devices.html", form=form, devices=rows, new_token=new_token, new_device=new_device,
                           revoke_form=EmptyForm())


@bp.route("/devices/<int:device_id>/revoke", methods=["POST"])
@login_required
def revoke(device_id: int):
    device = db.session.get(KioskDevice, device_id) or abort(404)
    if EmptyForm().validate_on_submit():
        kiosk_service.revoke_device(device, current_user.id)
        flash(f"Kiosk '{device.name}' revoked. It will be signed out on its next request.", "success")
    return redirect(url_for("kiosk.devices"))
