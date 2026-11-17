"""Flask CLI commands. Run ``flask --app wsgi <command> --help`` for details."""
from __future__ import annotations

import time
from datetime import timedelta

import click
import sqlalchemy as sa
from flask import Flask, current_app
from flask.cli import with_appcontext

from .extensions import db
from .models import WORKER_INACTIVE, Admin, Worker
from .services import audit
from .timeutil import now_utc

DEMO_WORKERS = [
    # Fictional metadata only. No face data is generated or downloaded.
    ("DEMO-001", "Asha Demo", "Masonry", "Acme Contractors (fictional)", "+91 90000 00001"),
    ("DEMO-002", "Ravi Example", "Masonry", "Acme Contractors (fictional)", None),
    ("DEMO-003", "Meena Sample", "Electrical", "Bright Line Services (fictional)", "+91 90000 00003"),
    ("DEMO-004", "Joseph Placeholder", "Carpentry", "Bright Line Services (fictional)", None),
    ("DEMO-005", "Farida Testcase", "Housekeeping", "CleanCo (fictional)", None),
    ("DEMO-006", "Gopal Mockname", "Loading", "Acme Contractors (fictional)", None),
]


def register(app: Flask) -> None:
    app.cli.add_command(create_admin)
    app.cli.add_command(seed_demo)
    app.cli.add_command(biometrics)
    app.cli.add_command(liveness_cli)
    app.cli.add_command(face_cli)


@click.command("create-admin")
@click.option("--username", prompt=True, help="Administrator username")
@click.password_option(help="Password (prompted, hidden, with confirmation)")
@with_appcontext
def create_admin(username: str, password: str) -> None:
    """Create an administrator account (use for the first admin)."""
    username = username.strip()
    if not 3 <= len(username) <= 64:
        raise click.BadParameter("username must be 3-64 characters", param_hint="--username")
    minimum = current_app.config["ADMIN_MIN_PASSWORD_LENGTH"]
    if len(password) < minimum:
        raise click.BadParameter(f"password must be at least {minimum} characters", param_hint="--password")
    if db.session.scalar(sa.select(Admin.id).where(Admin.username == username)):
        raise click.ClickException(f"administrator '{username}' already exists")
    admin = Admin(username=username)
    admin.set_password(password)
    db.session.add(admin)
    db.session.flush()
    audit.record("admin.create", actor_type="system", entity_type="admin", entity_id=admin.id,
                 after={"username": username})
    db.session.commit()
    click.echo(f"Administrator '{username}' created.")


@click.command("seed-demo")
@with_appcontext
def seed_demo() -> None:
    """Insert fictional demo workers (metadata only; no faces, no attendance)."""
    if current_app.config.get("ENV_NAME") == "production":
        raise click.ClickException("refusing to seed demo data in production")
    created = 0
    for code, name, dept, contractor, phone in DEMO_WORKERS:
        if db.session.scalar(sa.select(Worker.id).where(Worker.worker_code == code)):
            continue
        db.session.add(Worker(worker_code=code, full_name=name, department=dept, contractor=contractor,
                              phone=phone, created_at=now_utc()))
        created += 1
    audit.record("demo.seed", actor_type="system", after={"workers_created": created})
    db.session.commit()
    click.echo(f"Created {created} fictional demo workers ({len(DEMO_WORKERS) - created} already existed). "
               "Enroll faces through the web UI with consenting volunteers.")


@click.group("biometrics")
def biometrics() -> None:
    """Biometric data retention and deletion."""


@biometrics.command("purge-worker")
@click.argument("worker_code")
@click.option("--reason", required=True, help="Why the data is being deleted (audited)")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation")
@with_appcontext
def purge_worker(worker_code: str, reason: str, yes: bool) -> None:
    """Delete all face encodings (and any stored photos) for one worker."""
    from .services.enrollment import delete_face_data
    worker = db.session.scalar(sa.select(Worker).where(Worker.worker_code == worker_code.strip().upper()))
    if worker is None:
        raise click.ClickException(f"no worker with ID {worker_code}")
    if not yes:
        click.confirm(f"Permanently delete face data for {worker.worker_code}?", abort=True)
    n = delete_face_data(worker, reason=reason, actor_type="system")
    click.echo(f"Deleted {n} face samples for {worker.worker_code}.")


@biometrics.command("purge-inactive")
@click.option("--older-than-days", type=int, default=None,
              help="Days since deactivation (default: BIOMETRIC_RETENTION_DAYS)")
@click.option("--dry-run", is_flag=True)
@with_appcontext
def purge_inactive(older_than_days: int | None, dry_run: bool) -> None:
    """Delete face data of workers deactivated more than N days ago (retention policy)."""
    from .services.enrollment import delete_face_data
    days = older_than_days if older_than_days is not None else current_app.config["BIOMETRIC_RETENTION_DAYS"]
    cutoff = now_utc() - timedelta(days=days)
    workers = db.session.scalars(sa.select(Worker).where(
        Worker.status == WORKER_INACTIVE, Worker.deactivated_at.is_not(None), Worker.deactivated_at < cutoff,
        Worker.encodings.any())).all()
    total = 0
    for worker in workers:
        if dry_run:
            click.echo(f"would purge {worker.worker_code}")
            continue
        total += delete_face_data(worker, reason=f"Retention policy: inactive > {days} days", actor_type="system")
    click.echo(f"{'Dry run: ' if dry_run else ''}{len(workers)} workers matched; {total} samples deleted.")


@click.group("liveness")
def liveness_cli() -> None:
    """Liveness challenge maintenance."""


@liveness_cli.command("prune")
@click.option("--older-than-hours", type=int, default=24)
@with_appcontext
def prune(older_than_hours: int) -> None:
    """Delete expired liveness challenges."""
    from .services.kiosk import prune_challenges
    click.echo(f"Deleted {prune_challenges(older_than_hours)} expired challenges.")


@click.group("face")
def face_cli() -> None:
    """Face engine diagnostics."""


@face_cli.command("check-engine")
@with_appcontext
def check_engine() -> None:
    """Verify that dlib/face_recognition load and can process an image."""
    import numpy as np
    engine = current_app.extensions["face_engine"]
    start = time.perf_counter()
    boxes = engine.detect(np.full((480, 640, 3), 128, dtype=np.uint8))
    click.echo(f"face engine OK (model={current_app.config['FACE_DETECTION_MODEL']}); "
               f"blank test image -> {len(boxes)} faces in {time.perf_counter() - start:.2f}s")


@face_cli.command("check-camera")
@click.option("--index", type=int, default=0, help="OpenCV camera index on THIS machine")
@click.option("--frames", type=int, default=5)
@with_appcontext
def check_camera(index: int, frames: int) -> None:
    """Diagnose a webcam attached to the machine running this command (OpenCV capture).

    The kiosk itself uses the *browser's* camera; this is only a local hardware check.
    Frames are analysed in memory and never saved.
    """
    import cv2
    from .face.engine import analyze_frame
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise click.ClickException(f"could not open camera {index}. Is it connected and not in use by another app?")
    engine = current_app.extensions["face_engine"]
    try:
        for i in range(frames):
            ok, bgr = cap.read()
            if not ok:
                click.echo(f"frame {i + 1}: camera returned no image")
                continue
            ok_jpg, buf = cv2.imencode(".jpg", bgr)
            result = analyze_frame(engine, buf.tobytes(), current_app.config, want_encoding=False)
            click.echo(f"frame {i + 1}: {bgr.shape[1]}x{bgr.shape[0]} -> {result.code}"
                       + (f" ({result.message})" if not result.ok else "")
                       + (f" brightness={result.brightness:.0f}" if result.brightness is not None else ""))
            time.sleep(0.3)
    finally:
        cap.release()
