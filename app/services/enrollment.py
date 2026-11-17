"""Face enrollment, duplicate-identity review and biometric deletion."""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sqlalchemy as sa
from flask import current_app

from ..extensions import db
from ..face import codec
from ..face.engine import analyze_frame
from ..face.imaging import encode_jpeg
from ..models import (ENCODING_ACTIVE, ENCODING_PENDING, FLAG_APPROVED, FLAG_PENDING, FLAG_REJECTED,
                      EnrollmentFlag, FaceEncoding, Worker)
from ..timeutil import now_utc
from . import audit
from .recognition import invalidate_cache, nearest_other_workers

log = logging.getLogger(__name__)


class EnrollmentError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class SampleFeedback:
    index: int
    accepted: bool
    code: str
    message: str


@dataclass
class EnrollmentResult:
    stored: int = 0
    status: str = "rejected"  # enrolled | pending_review | rejected
    message: str = ""
    samples: list[SampleFeedback] = field(default_factory=list)
    flagged_worker_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status, "stored": self.stored, "message": self.message,
            "flagged": bool(self.flagged_worker_codes),
            "samples": [s.__dict__ for s in self.samples],
        }


def get_engine():
    return current_app.extensions["face_engine"]


def biometric_dir() -> Path:
    configured = current_app.config.get("BIOMETRIC_STORAGE_DIR")
    path = Path(configured) if configured else Path(current_app.instance_path) / "biometric"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def enroll(worker: Worker, images: list[bytes], *, consent_acknowledged: bool, admin_id: int) -> EnrollmentResult:
    cfg = current_app.config
    if not worker.is_active_worker:
        raise EnrollmentError("Inactive workers cannot be enrolled. Reactivate the worker first.")
    if worker.consent_given_at is None and not consent_acknowledged:
        raise EnrollmentError("The worker's consent acknowledgment is required before enrollment.")
    if not cfg["ENROLL_MIN_SAMPLES"] <= len(images) <= cfg["ENROLL_MAX_SAMPLES"]:
        raise EnrollmentError(f"Submit between {cfg['ENROLL_MIN_SAMPLES']} and {cfg['ENROLL_MAX_SAMPLES']} face samples.")

    engine = get_engine()
    result = EnrollmentResult()
    accepted: list[tuple[np.ndarray, object]] = []
    for i, raw in enumerate(images):
        analysis = analyze_frame(engine, raw, cfg, keep_rgb=cfg["STORE_ENROLLMENT_IMAGES"])
        if analysis.ok:
            accepted.append((analysis.face.encoding, analysis))
            result.samples.append(SampleFeedback(i, True, "ok", "Accepted"))
        else:
            result.samples.append(SampleFeedback(i, False, analysis.code, analysis.message))

    if len(accepted) < cfg["ENROLL_MIN_SAMPLES"]:
        result.message = (f"Only {len(accepted)} of {len(images)} samples were usable; at least "
                          f"{cfg['ENROLL_MIN_SAMPLES']} are required. Nothing was saved. Fix the issues shown and retake.")
        return result

    vectors = np.vstack([v for v, _ in accepted])
    pairwise = np.linalg.norm(vectors[:, None, :] - vectors[None, :, :], axis=2)
    if float(pairwise.max()) > cfg["ENROLL_SELF_CONSISTENCY_THRESHOLD"]:
        result.message = ("The samples do not appear to show the same person. Nothing was saved. "
                          "Retake all samples with only the worker in front of the camera.")
        return result

    # Possible enrollment under another worker ID? Flag for human review, never merge.
    matches: dict[int, float] = {}
    for vector in vectors:
        for other_id, dist in nearest_other_workers(vector, worker.id, cfg["ENROLL_DUPLICATE_THRESHOLD"]):
            matches[other_id] = min(dist, matches.get(other_id, np.inf))
    status = ENCODING_PENDING if matches else ENCODING_ACTIVE

    now = now_utc()
    if worker.consent_given_at is None:
        worker.consent_given_at = now
        worker.consent_notice_version = cfg["CONSENT_NOTICE_VERSION"]
        worker.consent_recorded_by_id = admin_id
        audit.record("worker.consent_recorded", entity_type="worker", entity_id=worker.id, worker_id=worker.id,
                     after={"notice_version": cfg["CONSENT_NOTICE_VERSION"]})

    written_files: list[Path] = []
    try:
        for vector, analysis in accepted:
            image_path = None
            if cfg["STORE_ENROLLMENT_IMAGES"] and analysis.rgb is not None:
                target = biometric_dir() / f"w{worker.id}_{secrets.token_hex(8)}.jpg"
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(encode_jpeg(analysis.rgb))
                written_files.append(target)
                image_path = target.name
            db.session.add(FaceEncoding(
                worker_id=worker.id, encoding=codec.encode(vector), status=status,
                face_size_px=analysis.face.size_px, brightness=analysis.brightness, sharpness=analysis.sharpness,
                image_path=image_path, created_by_id=admin_id, created_at=now))
        for other_id, dist in matches.items():
            exists = db.session.scalar(sa.select(EnrollmentFlag.id).where(
                EnrollmentFlag.worker_id == worker.id, EnrollmentFlag.matched_worker_id == other_id,
                EnrollmentFlag.status == FLAG_PENDING))
            if not exists:
                db.session.add(EnrollmentFlag(worker_id=worker.id, matched_worker_id=other_id,
                                              distance=round(float(dist), 4), created_at=now))
        flagged_codes = [w.worker_code for w in db.session.scalars(sa.select(Worker).where(Worker.id.in_(list(matches))))]
        audit.record("enrollment.add_samples", entity_type="worker", entity_id=worker.id, worker_id=worker.id,
                     after={"samples_stored": len(accepted), "status": status,
                            "raw_images_retained": bool(cfg["STORE_ENROLLMENT_IMAGES"]),
                            "flagged_against": flagged_codes})
        db.session.commit()
    except Exception:
        db.session.rollback()
        for path in written_files:
            path.unlink(missing_ok=True)
        raise
    invalidate_cache()

    result.stored = len(accepted)
    result.flagged_worker_codes = flagged_codes
    if matches:
        result.status = "pending_review"
        result.message = ("Samples saved but NOT active: they closely match another enrolled worker. "
                          "An administrator must review this under Enrollment reviews before this worker can be recognised.")
    else:
        result.status = "enrolled"
        result.message = f"Enrollment saved: {len(accepted)} face samples are active."
    return result


def _delete_files(paths: list[str | None]) -> None:
    base = biometric_dir()
    for name in paths:
        if name:
            # Only bare file names are stored; resolve inside the biometric directory.
            (base / Path(name).name).unlink(missing_ok=True)


def delete_face_data(worker: Worker, *, reason: str, actor_type: str = "admin") -> int:
    """Delete every stored encoding (and any retained photo) for a worker."""
    rows = list(db.session.scalars(sa.select(FaceEncoding).where(FaceEncoding.worker_id == worker.id)))
    files = [r.image_path for r in rows]
    count = len(rows)
    for row in rows:
        db.session.delete(row)
    for flag in db.session.scalars(sa.select(EnrollmentFlag).where(
            EnrollmentFlag.worker_id == worker.id, EnrollmentFlag.status == FLAG_PENDING)):
        flag.status, flag.resolved_at = FLAG_REJECTED, now_utc()
        flag.resolution_note = "Face data deleted"
    audit.record("enrollment.delete_face_data", actor_type=actor_type, entity_type="worker", entity_id=worker.id,
                 worker_id=worker.id, reason=reason, before={"encodings": count}, after={"encodings": 0})
    db.session.commit()
    _delete_files(files)
    invalidate_cache()
    return count


def resolve_flag(flag: EnrollmentFlag, *, approve: bool, note: str, admin_id: int) -> None:
    """Approve: the flagged worker's pending encodings become active (the admin has confirmed
    these are different people). Reject: the pending encodings are deleted. Identities are
    never merged."""
    if flag.status != FLAG_PENDING:
        raise EnrollmentError("This review has already been resolved.")
    if len((note or "").strip()) < 5:
        raise EnrollmentError("A resolution note (at least 5 characters) is required.")
    now = now_utc()
    flag.status = FLAG_APPROVED if approve else FLAG_REJECTED
    flag.resolved_at, flag.resolved_by_id, flag.resolution_note = now, admin_id, note.strip()
    files: list[str | None] = []
    affected = 0
    if approve:
        still_pending = db.session.scalar(sa.select(sa.func.count(EnrollmentFlag.id)).where(
            EnrollmentFlag.worker_id == flag.worker_id, EnrollmentFlag.status == FLAG_PENDING,
            EnrollmentFlag.id != flag.id))
        if not still_pending:
            for enc in db.session.scalars(sa.select(FaceEncoding).where(
                    FaceEncoding.worker_id == flag.worker_id, FaceEncoding.status == ENCODING_PENDING)):
                enc.status = ENCODING_ACTIVE
                affected += 1
    else:
        for enc in db.session.scalars(sa.select(FaceEncoding).where(
                FaceEncoding.worker_id == flag.worker_id, FaceEncoding.status == ENCODING_PENDING)):
            files.append(enc.image_path)
            db.session.delete(enc)
            affected += 1
        # Other open flags for the same pending samples are moot once they are deleted.
        for other in db.session.scalars(sa.select(EnrollmentFlag).where(
                EnrollmentFlag.worker_id == flag.worker_id, EnrollmentFlag.status == FLAG_PENDING,
                EnrollmentFlag.id != flag.id)):
            other.status, other.resolved_at, other.resolved_by_id = FLAG_REJECTED, now, admin_id
            other.resolution_note = f"Pending samples rejected in review #{flag.id}"
    audit.record("enrollment.review_" + ("approve" if approve else "reject"), admin_id=admin_id,
                 entity_type="enrollment_flag", entity_id=flag.id, worker_id=flag.worker_id, reason=note.strip(),
                 before={"status": FLAG_PENDING}, after={"status": flag.status, "encodings_affected": affected,
                                                         "matched_worker_id": flag.matched_worker_id})
    db.session.commit()
    _delete_files(files)
    invalidate_cache()
