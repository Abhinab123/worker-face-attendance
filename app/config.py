"""Configuration classes.

Values are read from environment variables when a config object is constructed
(after ``.env`` has been loaded), never at import time. See ``.env.example``.
"""
from __future__ import annotations

import os
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PLACEHOLDER_SECRETS = {"", "change-me", "replace-with-a-long-random-value"}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    value = _env(name)
    return default if value is None else float(value)


def _int(name: str, default: int) -> int:
    value = _env(name)
    return default if value is None else int(value)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class BaseConfig:
    ENV_NAME = "base"

    def __init__(self) -> None:
        self.SECRET_KEY = _env("SECRET_KEY")
        self.SQLALCHEMY_DATABASE_URI = _env("DATABASE_URL")
        self.SQLALCHEMY_TRACK_MODIFICATIONS = False
        self.SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

        # Request limits: a kiosk verification posts ~16 compressed frames.
        self.MAX_CONTENT_LENGTH = _int("MAX_CONTENT_LENGTH", 16 * 1024 * 1024)
        self.MAX_IMAGE_BYTES = _int("MAX_IMAGE_BYTES", 2 * 1024 * 1024)
        self.MIN_IMAGE_DIM = _int("MIN_IMAGE_DIM", 240)
        self.MAX_IMAGE_DIM = _int("MAX_IMAGE_DIM", 1920)
        self.PROCESSING_MAX_WIDTH = _int("PROCESSING_MAX_WIDTH", 640)

        # Sessions / cookies
        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = "Lax"
        self.SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
        self.REMEMBER_COOKIE_SECURE = self.SESSION_COOKIE_SECURE
        self.PERMANENT_SESSION_LIFETIME = timedelta(hours=_int("KIOSK_SESSION_HOURS", 12))
        self.WTF_CSRF_TIME_LIMIT = None  # tied to the session instead of a fixed clock

        # Authentication hardening
        self.LOGIN_MAX_ATTEMPTS = _int("LOGIN_MAX_ATTEMPTS", 5)
        self.LOGIN_LOCKOUT_MINUTES = _int("LOGIN_LOCKOUT_MINUTES", 15)
        self.ADMIN_MIN_PASSWORD_LENGTH = _int("ADMIN_MIN_PASSWORD_LENGTH", 12)

        # Time
        self.BUSINESS_TIMEZONE = _env("BUSINESS_TIMEZONE", "Asia/Kolkata")

        # Face detection / recognition
        self.FACE_DETECTION_MODEL = _env("FACE_DETECTION_MODEL", "hog")  # "hog" (CPU) or "cnn" (GPU)
        self.FACE_DETECTION_UPSAMPLE = _int("FACE_DETECTION_UPSAMPLE", 1)
        self.FACE_ENCODING_JITTERS = _int("FACE_ENCODING_JITTERS", 1)
        self.FACE_MATCH_THRESHOLD = _float("FACE_MATCH_THRESHOLD", 0.50)
        self.FACE_AMBIGUITY_MARGIN = _float("FACE_AMBIGUITY_MARGIN", 0.06)
        self.ENROLL_DUPLICATE_THRESHOLD = _float("ENROLL_DUPLICATE_THRESHOLD", 0.45)
        self.ENROLL_SELF_CONSISTENCY_THRESHOLD = _float("ENROLL_SELF_CONSISTENCY_THRESHOLD", 0.60)
        self.ENROLL_MIN_SAMPLES = _int("ENROLL_MIN_SAMPLES", 3)
        self.ENROLL_MAX_SAMPLES = _int("ENROLL_MAX_SAMPLES", 8)
        self.MIN_FACE_SIZE_PX = _int("MIN_FACE_SIZE_PX", 80)
        self.MIN_BRIGHTNESS = _float("MIN_BRIGHTNESS", 50.0)
        self.MAX_BRIGHTNESS = _float("MAX_BRIGHTNESS", 215.0)
        self.MIN_SHARPNESS = _float("MIN_SHARPNESS", 20.0)
        self.RECOGNITION_MIN_MATCHED_FRAMES = _int("RECOGNITION_MIN_MATCHED_FRAMES", 3)
        self.RECOGNITION_COOLDOWN_SECONDS = _int("RECOGNITION_COOLDOWN_SECONDS", 20)

        # Liveness challenge
        self.LIVENESS_REQUIRED = _bool("LIVENESS_REQUIRED", True)
        self.LIVENESS_CHALLENGE_TTL_SECONDS = _int("LIVENESS_CHALLENGE_TTL_SECONDS", 30)
        self.LIVENESS_CHALLENGE_TYPES = (_env("LIVENESS_CHALLENGE_TYPES", "blink,turn_left,turn_right") or "").split(",")
        self.LIVENESS_FRAME_COUNT = _int("LIVENESS_FRAME_COUNT", 16)
        self.LIVENESS_FRAME_INTERVAL_MS = _int("LIVENESS_FRAME_INTERVAL_MS", 180)
        self.LIVENESS_MIN_FACE_FRAMES = _int("LIVENESS_MIN_FACE_FRAMES", 5)
        self.LIVENESS_EAR_MIN_OPEN = _float("LIVENESS_EAR_MIN_OPEN", 0.20)
        self.LIVENESS_BLINK_RATIO = _float("LIVENESS_BLINK_RATIO", 0.70)
        self.LIVENESS_YAW_CENTER_TOLERANCE = _float("LIVENESS_YAW_CENTER_TOLERANCE", 0.08)
        self.LIVENESS_YAW_DELTA = _float("LIVENESS_YAW_DELTA", 0.12)

        # Biometric data handling
        self.STORE_ENROLLMENT_IMAGES = _bool("STORE_ENROLLMENT_IMAGES", False)
        self.BIOMETRIC_STORAGE_DIR = _env("BIOMETRIC_STORAGE_DIR")  # default: <instance>/biometric
        self.BIOMETRIC_RETENTION_DAYS = _int("BIOMETRIC_RETENTION_DAYS", 30)
        self.CONSENT_NOTICE_VERSION = _env("CONSENT_NOTICE_VERSION", "2026-11")

        # Admin corrections
        self.CORRECTION_REASON_MIN_LENGTH = _int("CORRECTION_REASON_MIN_LENGTH", 10)

        # Development server
        self.HOST = _env("HOST", "127.0.0.1")
        self.PORT = _int("PORT", 5000)


class DevelopmentConfig(BaseConfig):
    ENV_NAME = "development"


class ProductionConfig(BaseConfig):
    ENV_NAME = "production"

    def __init__(self) -> None:
        super().__init__()
        # Production must sit behind HTTPS; secure cookies are mandatory.
        self.SESSION_COOKIE_SECURE = True
        self.REMEMBER_COOKIE_SECURE = True


class TestingConfig(BaseConfig):
    ENV_NAME = "testing"

    def __init__(self) -> None:
        super().__init__()
        self.TESTING = True
        self.SECRET_KEY = "test-secret-key-" + "x" * 32
        self.SQLALCHEMY_DATABASE_URI = _env("TEST_DATABASE_URL", "sqlite://")
        self.WTF_CSRF_ENABLED = True
        self.LIVENESS_REQUIRED = True
        self.RECOGNITION_COOLDOWN_SECONDS = 0
        self.BUSINESS_TIMEZONE = "Asia/Kolkata"
        self.MIN_SHARPNESS = 5.0
        self.LOGIN_MAX_ATTEMPTS = 3


def validate_config(c) -> None:
    """Validate the final (merged) Flask config mapping."""
    if not c["SQLALCHEMY_DATABASE_URI"]:
        raise ConfigError("DATABASE_URL is not set. Copy .env.example to .env and configure it.")
    if (c["SECRET_KEY"] or "") in PLACEHOLDER_SECRETS or len(c["SECRET_KEY"] or "") < 32:
        raise ConfigError(
            "SECRET_KEY is missing, a placeholder, or shorter than 32 characters. Generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    try:
        ZoneInfo(c["BUSINESS_TIMEZONE"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("BUSINESS_TIMEZONE '%s' is not a valid IANA zone" % c["BUSINESS_TIMEZONE"]) from exc
    if c["FACE_DETECTION_MODEL"] not in {"hog", "cnn"}:
        raise ConfigError("FACE_DETECTION_MODEL must be 'hog' or 'cnn'")
    if not 0.0 < c["FACE_MATCH_THRESHOLD"] < 1.0:
        raise ConfigError("FACE_MATCH_THRESHOLD must be between 0 and 1")
    if c["ENROLL_MIN_SAMPLES"] < 1 or c["ENROLL_MAX_SAMPLES"] < c["ENROLL_MIN_SAMPLES"]:
        raise ConfigError("ENROLL_MIN_SAMPLES/ENROLL_MAX_SAMPLES are inconsistent")
    unknown = set(c["LIVENESS_CHALLENGE_TYPES"]) - {"blink", "turn_left", "turn_right"}
    if unknown or not c["LIVENESS_CHALLENGE_TYPES"]:
        raise ConfigError(f"LIVENESS_CHALLENGE_TYPES contains unsupported values: {sorted(unknown)}")


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name: str | None = None) -> BaseConfig:
    env_name = name or _env("APP_ENV", "development")
    if env_name not in CONFIGS:
        raise ConfigError(f"APP_ENV must be one of {sorted(CONFIGS)}")
    return CONFIGS[env_name]()
