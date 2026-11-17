"""Kiosk devices, liveness challenges and the end-to-end attendance verification pipeline."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta

import sqlalchemy as sa
from flask import current_app

from ..extensions import db
from ..face import liveness
from ..face.engine import analyze_frame
from ..face.imaging import ImageLimits, ImageRejected, decode_data_url
from ..models import METHOD_FACE, MODE_CHECK_IN, MODE_CHECK_OUT, KioskDevice, LivenessChallenge, Worker
from ..timeutil import format_local, now_utc
from . import attendance, audit
from .recognition import AMBIGUOUS, MATCH, NO_ENROLLMENTS, match_sequence


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- devices

def create_device(name: str, admin_id: int) -> tuple[KioskDevice, str]:
    name = " ".join((name or "").split())
    if not 2 <= len(name) <= 80:
        raise ValueError("Device name must be 2-80 characters.")
    token = secrets.token_urlsafe(24)
    device = KioskDevice(name=name, token_hash=hash_token(token), created_by_id=admin_id, created_at=now_utc())
    db.session.add(device)
    db.session.flush()
    audit.record("kiosk.device_create", admin_id=admin_id, entity_type="kiosk_device", entity_id=device.id,
                 after={"name": name})
    db.session.commit()
    return device, token  # plaintext token is shown once and never stored


def activate_device(token: str) -> KioskDevice | None:
    token = (token or "").strip()
    if not token:
        return None
    device = db.session.scalar(sa.select(KioskDevice).where(KioskDevice.token_hash == hash_token(token)))
    if device is None or device.is_revoked:
        return None
    device.activated_at = device.last_seen_at = now_utc()
    audit.record("kiosk.device_activate", actor_type="kiosk", kiosk_device_id=device.id,
                 entity_type="kiosk_device", entity_id=device.id)
    db.session.commit()
    return device


def revoke_device(device: KioskDevice, admin_id: int) -> None:
    if device.revoked_at is None:
        device.revoked_at = now_utc()
        audit.record("kiosk.device_revoke", admin_id=admin_id, entity_type="kiosk_device", entity_id=device.id,
                     before={"revoked": False}, after={"revoked": True})
        db.session.commit()


# ---------------------------------------------------------------- challenges

@dataclass
class KioskContext:
    session_id: str
    device_id: int | None
    admin_id: int | None


def issue_challenge(ctx: KioskContext, mode: str) -> tuple[LivenessChallenge, str]:
    cfg = current_app.config
    if mode not in {MODE_CHECK_IN, MODE_CHECK_OUT}:
        raise ValueError("mode must be check_in or check_out")
    ctype = secrets.choice(cfg["LIVENESS_CHALLENGE_TYPES"]) if cfg["LIVENESS_REQUIRED"] else "none"
    token = secrets.token_urlsafe(32)
    now = now_utc()
    challenge = LivenessChallenge(
        token_hash=hash_token(token), kiosk_session_id=ctx.session_id, kiosk_device_id=ctx.device_id,
        admin_id=ctx.admin_id, challenge_type=ctype, mode=mode, issued_at=now,
        expires_at=now + timedelta(seconds=cfg["LIVENESS_CHALLENGE_TTL_SECONDS"]))
    db.session.add(challenge)
    db.session.commit()
    return challenge, token


class ChallengeRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def consume_challenge(ctx: KioskContext, token: str, mode: str) -> LivenessChallenge:
    """Atomically mark a challenge used. Single UPDATE guarded by consumed_at IS NULL, so a
    replayed token (or two concurrent submissions) can succeed at most once."""
    if not isinstance(token, str) or not token:
        raise ChallengeRejected("challenge_invalid", "Missing challenge. Start again.")
    token_hash = hash_token(token)
    now = now_utc()
    result = db.session.execute(
        sa.update(LivenessChallenge)
        .where(LivenessChallenge.token_hash == token_hash, LivenessChallenge.consumed_at.is_(None),
               LivenessChallenge.kiosk_session_id == ctx.session_id, LivenessChallenge.expires_at >= now,
               LivenessChallenge.mode == mode)
        .values(consumed_at=now, outcome="processing")
        .execution_options(synchronize_session=False))
    db.session.commit()
    challenge = db.session.scalar(sa.select(LivenessChallenge).where(LivenessChallenge.token_hash == token_hash))
    if result.rowcount == 1:
        return challenge
    if challenge is None or challenge.kiosk_session_id != ctx.session_id:
        raise ChallengeRejected("challenge_invalid", "This challenge is not valid for this kiosk session. Start again.")
    if challenge.consumed_at is not None:
        raise ChallengeRejected("challenge_replayed", "This challenge was already used. Start again.")
    if challenge.mode != mode:
        raise ChallengeRejected("challenge_invalid", "Challenge mode mismatch. Start again.")
    raise ChallengeRejected("challenge_expired", "The challenge expired. Start again and respond within the time shown.")


def _finish(challenge: LivenessChallenge, outcome: str) -> None:
    challenge.outcome = outcome[:32]
    db.session.commit()


# ---------------------------------------------------------------- cooldown

class Cooldown:
    """Suppresses repeat notifications for the same worker/mode for a few seconds.
    Process-local; correctness never depends on it (the database enforces uniqueness)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[tuple[int, str], tuple[float, str]] = {}

    def recent(self, worker_id: int, mode: str, seconds: int) -> str | None:
        with self._lock:
            entry = self._seen.get((worker_id, mode))
            if entry and time.monotonic() - entry[0] < seconds:
                return entry[1]
            return None

    def mark(self, worker_id: int, mode: str, message: str) -> None:
        with self._lock:
            self._seen[(worker_id, mode)] = (time.monotonic(), message)
            if len(self._seen) > 5000:
                cutoff = time.monotonic() - 3600
                self._seen = {k: v for k, v in self._seen.items() if v[0] > cutoff}


# ---------------------------------------------------------------- verification

@dataclass
class KioskOutcome:
    ok: bool
    status: str
    title: str
    message: str
    worker: dict | None = None
    time_local: str | None = None
    hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "status": self.status, "title": self.title, "message": self.message,
                "worker": self.worker, "time": self.time_local, "hints": self.hints}


def _fail(challenge, ctx, status, title, message, hints=None, worker_id=None) -> KioskOutcome:
    _finish(challenge, status)
    audit.record("kiosk.attempt_rejected", actor_type="kiosk", admin_id=ctx.admin_id, kiosk_device_id=ctx.device_id,
                 entity_type="liveness_challenge", entity_id=challenge.id, worker_id=worker_id,
                 after={"outcome": status, "mode": challenge.mode, "challenge_type": challenge.challenge_type})
    db.session.commit()
    return KioskOutcome(False, status, title, message, hints=hints or [])


def verify_attendance(ctx: KioskContext, token: str, mode: str, frames: list) -> KioskOutcome:
    cfg = current_app.config
    try:
        challenge = consume_challenge(ctx, token, mode)
    except ChallengeRejected as rej:
        return KioskOutcome(False, rej.code, "Please try again", rej.message)

    if not isinstance(frames, list) or not 1 <= len(frames) <= cfg["LIVENESS_FRAME_COUNT"] + 4:
        return _fail(challenge, ctx, "invalid_request", "Please try again", "Camera frames were missing. Start again.")

    limits = ImageLimits.from_config(cfg)
    engine = current_app.extensions["face_engine"]
    encodings, landmark_seq, problems = [], [], {}
    for frame in frames:
        try:
            raw = decode_data_url(frame, limits)
        except ImageRejected as rej:
            return _fail(challenge, ctx, rej.code, "Camera problem", rej.message)
        analysis = analyze_frame(engine, raw, cfg, want_landmarks=True)
        if analysis.code == "multiple_faces":
            return _fail(challenge, ctx, "multiple_faces", "Only one person, please",
                         "More than one face was seen. Only the worker recording attendance should be in view.")
        if not analysis.ok:
            problems[analysis.code] = analysis.message
            continue
        encodings.append(analysis.face.encoding)
        if analysis.face.landmarks:
            landmark_seq.append(analysis.face.landmarks)

    if len(encodings) < cfg["LIVENESS_MIN_FACE_FRAMES"]:
        hints = list(problems.values())[:3] or ["Keep your face inside the frame and hold the device steady."]
        status = "poor_lighting" if {"too_dark", "too_bright"} & set(problems) else "no_face"
        return _fail(challenge, ctx, status, "Face not clearly visible",
                     "Your face was not clearly visible in enough frames.", hints)

    seq = match_sequence(encodings)
    if seq.status == NO_ENROLLMENTS:
        return _fail(challenge, ctx, "no_enrollments", "No enrolled workers",
                     "No active face enrollments exist yet. An administrator must enroll workers first.")
    if seq.status == AMBIGUOUS:
        return _fail(challenge, ctx, "ambiguous", "Could not identify reliably",
                     "The face matched more than one worker closely. Attendance was not recorded. "
                     "Please see the supervisor.")
    if seq.status != MATCH:
        return _fail(challenge, ctx, "unknown_face", "Face not recognised",
                     "You were not recognised as an enrolled, active worker. Attendance was not recorded.",
                     ["Remove caps, masks or sunglasses and face the camera directly.",
                      "If you are newly registered, ask the supervisor to complete your enrollment."])

    if challenge.challenge_type != "none":
        live = liveness.evaluate(challenge.challenge_type, landmark_seq, cfg)
        if not live.passed:
            return _fail(challenge, ctx, "liveness_failed", "Challenge not completed",
                         f"The requested action was not detected. {liveness.INSTRUCTIONS[challenge.challenge_type]}",
                         ["Perform the action slowly while keeping your face in the frame."], worker_id=seq.worker_id)

    worker = db.session.get(Worker, seq.worker_id)
    if worker is None or not worker.is_active_worker:
        return _fail(challenge, ctx, "inactive_worker", "Not allowed",
                     "This worker is not active. Please see the supervisor.", worker_id=seq.worker_id)
    who = {"worker_code": worker.worker_code, "full_name": worker.full_name}

    cooldown: Cooldown = current_app.extensions["kiosk_cooldown"]
    recent = cooldown.recent(worker.id, mode, cfg["RECOGNITION_COOLDOWN_SECONDS"])
    if recent:
        _finish(challenge, "cooldown")
        return KioskOutcome(True, "cooldown", "Already recorded", recent, worker=who)

    try:
        if mode == MODE_CHECK_IN:
            record = attendance.check_in(worker, method=METHOD_FACE, distance=seq.distance,
                                         device_id=ctx.device_id, admin_id=ctx.admin_id)
            when, verb = record.check_in_at, "Checked in"
        else:
            record = attendance.check_out(worker, method=METHOD_FACE, distance=seq.distance,
                                          device_id=ctx.device_id, admin_id=ctx.admin_id)
            when, verb = record.check_out_at, "Checked out"
    except attendance.AttendanceError as err:
        _finish(challenge, err.code)
        return KioskOutcome(False, err.code, "Not recorded", err.message, worker=who)

    time_local = format_local(when, "%H:%M:%S")
    message = f"{verb} at {time_local}."
    cooldown.mark(worker.id, mode, message)
    _finish(challenge, "recorded")
    return KioskOutcome(True, "recorded", f"{verb}: {worker.full_name}", message, worker=who, time_local=time_local)


def prune_challenges(older_than_hours: int = 24) -> int:
    cutoff = now_utc() - timedelta(hours=older_than_hours)
    result = db.session.execute(sa.delete(LivenessChallenge).where(LivenessChallenge.expires_at < cutoff))
    db.session.commit()
    return result.rowcount or 0
