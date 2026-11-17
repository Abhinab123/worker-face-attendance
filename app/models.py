"""Database models. Schema documentation: docs/database.md."""
from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from flask_login import UserMixin
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .timeutil import utcnow

# Microsecond precision on MySQL (plain DATETIME truncates to seconds).
UTCDateTime = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")

WORKER_ACTIVE = "active"
WORKER_INACTIVE = "inactive"

ENCODING_ACTIVE = "active"
ENCODING_PENDING = "pending_review"

FLAG_PENDING = "pending"
FLAG_APPROVED = "approved"
FLAG_REJECTED = "rejected"

METHOD_FACE = "face"
METHOD_ADMIN = "admin"

MODE_CHECK_IN = "check_in"
MODE_CHECK_OUT = "check_out"


class Admin(UserMixin, db.Model):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(sa.String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    is_enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True, server_default=sa.true())
    failed_login_count: Mapped[int] = mapped_column(sa.Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)  # scrypt (Werkzeug 3 default)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:  # Flask-Login hook
        return bool(self.is_enabled)


class Worker(db.Model):
    __tablename__ = "workers"
    __table_args__ = (
        sa.CheckConstraint("status IN ('active', 'inactive')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_code: Mapped[str] = mapped_column(sa.String(32), unique=True)
    full_name: Mapped[str] = mapped_column(sa.String(120), index=True)
    department: Mapped[str] = mapped_column(sa.String(80), index=True)
    contractor: Mapped[str] = mapped_column(sa.String(120), index=True)
    phone: Mapped[str | None] = mapped_column(sa.String(20))
    status: Mapped[str] = mapped_column(sa.String(16), default=WORKER_ACTIVE, server_default=WORKER_ACTIVE, index=True)
    consent_given_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    consent_notice_version: Mapped[str | None] = mapped_column(sa.String(16))
    consent_recorded_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("admins.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    deactivated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    encodings: Mapped[list["FaceEncoding"]] = relationship(back_populates="worker", cascade="all, delete-orphan", passive_deletes=True)

    @property
    def is_active_worker(self) -> bool:
        return self.status == WORKER_ACTIVE


class FaceEncoding(db.Model):
    """A 128-d dlib face descriptor stored as 1024 raw bytes (little-endian float64).

    Never pickled: see app/face/codec.py.
    """

    __tablename__ = "face_encodings"
    __table_args__ = (
        sa.CheckConstraint("status IN ('active', 'pending_review')", name="status_valid"),
        sa.Index("ix_face_encodings_status_worker", "status", "worker_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_id: Mapped[int] = mapped_column(sa.ForeignKey("workers.id", ondelete="CASCADE"), index=True)
    encoding: Mapped[bytes] = mapped_column(sa.LargeBinary)
    model_name: Mapped[str] = mapped_column(sa.String(32), default="dlib_resnet_v1")
    status: Mapped[str] = mapped_column(sa.String(16), default=ENCODING_ACTIVE)
    face_size_px: Mapped[int | None] = mapped_column(sa.Integer)
    brightness: Mapped[float | None] = mapped_column(sa.Float)
    sharpness: Mapped[float | None] = mapped_column(sa.Float)
    image_path: Mapped[str | None] = mapped_column(sa.String(255))  # only if STORE_ENROLLMENT_IMAGES
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("admins.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    worker: Mapped[Worker] = relationship(back_populates="encodings")


class EnrollmentFlag(db.Model):
    """Possible enrollment of the same person under another worker ID. Resolved by an admin only."""

    __tablename__ = "enrollment_flags"
    __table_args__ = (
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_id: Mapped[int] = mapped_column(sa.ForeignKey("workers.id", ondelete="CASCADE"), index=True)
    matched_worker_id: Mapped[int] = mapped_column(sa.ForeignKey("workers.id", ondelete="CASCADE"), index=True)
    distance: Mapped[float] = mapped_column(sa.Float)
    status: Mapped[str] = mapped_column(sa.String(16), default=FLAG_PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("admins.id"))
    resolution_note: Mapped[str | None] = mapped_column(sa.Text)

    worker: Mapped[Worker] = relationship(foreign_keys=[worker_id])
    matched_worker: Mapped[Worker] = relationship(foreign_keys=[matched_worker_id])
    resolved_by: Mapped[Admin | None] = relationship()


class KioskDevice(db.Model):
    """A browser explicitly enrolled by an admin to act as an attendance kiosk."""

    __tablename__ = "kiosk_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(80))
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)  # SHA-256 hex of activation token
    created_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("admins.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class LivenessChallenge(db.Model):
    """Short-lived, single-use, server-issued challenge bound to one kiosk browser session."""

    __tablename__ = "liveness_challenges"
    __table_args__ = (
        sa.CheckConstraint("challenge_type IN ('blink', 'turn_left', 'turn_right', 'none')", name="type_valid"),
        sa.CheckConstraint("mode IN ('check_in', 'check_out')", name="mode_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    kiosk_session_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    kiosk_device_id: Mapped[int | None] = mapped_column(sa.ForeignKey("kiosk_devices.id", ondelete="SET NULL"))
    admin_id: Mapped[int | None] = mapped_column(sa.ForeignKey("admins.id", ondelete="SET NULL"))
    challenge_type: Mapped[str] = mapped_column(sa.String(16))
    mode: Mapped[str] = mapped_column(sa.String(16))
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    outcome: Mapped[str | None] = mapped_column(sa.String(32))


class AttendanceRecord(db.Model):
    """At most one record per worker per business date (enforced by a UNIQUE constraint)."""

    __tablename__ = "attendance_records"
    __table_args__ = (
        sa.UniqueConstraint("worker_id", "business_date", name="uq_attendance_worker_date"),
        sa.CheckConstraint("check_out_at IS NULL OR check_out_at >= check_in_at", name="checkout_after_checkin"),
        sa.CheckConstraint("check_in_method IN ('face', 'admin')", name="check_in_method_valid"),
        sa.CheckConstraint("check_out_method IS NULL OR check_out_method IN ('face', 'admin')", name="check_out_method_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_id: Mapped[int] = mapped_column(sa.ForeignKey("workers.id", ondelete="RESTRICT"))
    business_date: Mapped[date] = mapped_column(sa.Date, index=True)
    check_in_at: Mapped[datetime] = mapped_column(UTCDateTime)
    check_out_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    check_in_method: Mapped[str] = mapped_column(sa.String(16))
    check_out_method: Mapped[str | None] = mapped_column(sa.String(16))
    check_in_distance: Mapped[float | None] = mapped_column(sa.Float)
    check_out_distance: Mapped[float | None] = mapped_column(sa.Float)
    check_in_device_id: Mapped[int | None] = mapped_column(sa.ForeignKey("kiosk_devices.id", ondelete="SET NULL"))
    check_out_device_id: Mapped[int | None] = mapped_column(sa.ForeignKey("kiosk_devices.id", ondelete="SET NULL"))
    is_corrected: Mapped[bool] = mapped_column(sa.Boolean, default=False, server_default=sa.false())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    worker: Mapped[Worker] = relationship()

    @property
    def duration_minutes(self) -> int | None:
        if self.check_out_at is None:
            return None
        return int((self.check_out_at - self.check_in_at).total_seconds() // 60)

    def snapshot(self) -> dict:
        return {
            "business_date": self.business_date.isoformat() if self.business_date else None,
            "check_in_at_utc": self.check_in_at.isoformat() if self.check_in_at else None,
            "check_out_at_utc": self.check_out_at.isoformat() if self.check_out_at else None,
            "check_in_method": self.check_in_method,
            "check_out_method": self.check_out_method,
        }


class AuditEvent(db.Model):
    """Append-only audit trail. Never stores images, encodings or secrets."""

    __tablename__ = "audit_events"
    __table_args__ = (sa.Index("ix_audit_events_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(sa.String(16))  # admin | kiosk | system
    actor_admin_id: Mapped[int | None] = mapped_column(sa.ForeignKey("admins.id", ondelete="SET NULL"))
    kiosk_device_id: Mapped[int | None] = mapped_column(sa.ForeignKey("kiosk_devices.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(sa.String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(sa.String(32))
    entity_id: Mapped[str | None] = mapped_column(sa.String(64))
    worker_id: Mapped[int | None] = mapped_column(sa.ForeignKey("workers.id", ondelete="SET NULL"), index=True)
    reason: Mapped[str | None] = mapped_column(sa.Text)
    before_data: Mapped[dict | None] = mapped_column(sa.JSON)
    after_data: Mapped[dict | None] = mapped_column(sa.JSON)
    ip_address: Mapped[str | None] = mapped_column(sa.String(45))

    actor_admin: Mapped[Admin | None] = relationship()
    worker: Mapped[Worker | None] = relationship()
    kiosk_device: Mapped[KioskDevice | None] = relationship()
