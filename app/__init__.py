"""Worker Face Attendance: Flask application factory."""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_wtf.csrf import CSRFError

from .config import get_config, validate_config
from .extensions import csrf, db, login_manager, migrate
from .face.engine import DlibFaceEngine
from .services.kiosk import Cooldown
from .services.recognition import EncodingCache
from .timeutil import format_local, to_local, utcnow


def create_app(config_name: str | None = None, *, config_overrides: dict | None = None, face_engine=None) -> Flask:
    if config_name != "testing":
        load_dotenv(override=False)
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))
    if config_overrides:
        app.config.update(config_overrides)
    validate_config(app.config)
    os.makedirs(app.instance_path, mode=0o700, exist_ok=True)

    _configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db, compare_type=True, render_as_batch=True)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "warning"
    login_manager.session_protection = "strong"

    app.extensions["face_engine"] = face_engine or DlibFaceEngine(
        model=app.config["FACE_DETECTION_MODEL"], upsample=app.config["FACE_DETECTION_UPSAMPLE"],
        jitters=app.config["FACE_ENCODING_JITTERS"])
    app.extensions["encoding_cache"] = EncodingCache()
    app.extensions["kiosk_cooldown"] = Cooldown()
    app.extensions.setdefault("clock", utcnow)

    from .models import Admin

    @login_manager.user_loader
    def load_user(user_id: str):
        admin = db.session.get(Admin, int(user_id)) if user_id.isdigit() else None
        return admin if admin and admin.is_enabled else None

    from .blueprints import attendance, audit_log, auth, kiosk, main, reviews, workers
    for bp in (auth.bp, main.bp, workers.bp, reviews.bp, attendance.bp, audit_log.bp, kiosk.bp):
        app.register_blueprint(bp)

    from . import cli
    cli.register(app)

    _register_template_helpers(app)
    _register_error_handlers(app)
    _register_security_headers(app)
    return app


def _configure_logging(app: Flask) -> None:
    # Never log request bodies: they contain face images. Werkzeug logs only method/path/status.
    level = logging.DEBUG if app.debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _register_template_helpers(app: Flask) -> None:
    app.jinja_env.filters["localtime"] = lambda v, fmt="%Y-%m-%d %H:%M:%S": format_local(v, fmt)
    app.jinja_env.filters["localdt"] = to_local

    @app.context_processor
    def inject_globals():
        return {"business_timezone": app.config["BUSINESS_TIMEZONE"]}


def _wants_json() -> bool:
    return request.path.startswith("/kiosk/api/") or request.is_json or (
        request.accept_mimetypes.best == "application/json")


def _register_error_handlers(app: Flask) -> None:
    def render(status: int, title: str, message: str):
        if _wants_json():
            return jsonify(ok=False, status="error", title=title, message=message), status
        return render_template("errors/error.html", status=status, title=title, message=message), status

    @app.errorhandler(CSRFError)
    def csrf_error(_err):
        return render(400, "Session expired", "Your form session expired or is invalid. Reload the page and try again.")

    @app.errorhandler(403)
    def forbidden(_err):
        return render(403, "Not allowed", "You do not have access to this page.")

    @app.errorhandler(404)
    def not_found(_err):
        return render(404, "Not found", "The page you requested does not exist.")

    @app.errorhandler(413)
    def too_large(_err):
        return render(413, "Upload too large", "The images sent were too large. Lower the camera resolution and try again.")

    @app.errorhandler(500)
    def server_error(_err):
        db.session.rollback()
        return render(500, "Server error", "Something went wrong. The error was logged; please try again.")


def _register_security_headers(app: Flask) -> None:
    csp = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob: mediastream:; "
           "script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
           "form-action 'self'; base-uri 'self'; object-src 'none'")

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("Content-Security-Policy", csp)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        if not request.path.startswith("/static/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp
