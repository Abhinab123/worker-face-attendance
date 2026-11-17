import logging

import pytest

from app.config import ConfigError, validate_config
from app.config import TestingConfig as _TestingConfig
from app.face.imaging import ImageLimits, ImageRejected, decode_data_url, load_rgb
from app.security import safe_next_url

from .fakes import FakeFace, Person, data_url, make_png


def test_health_endpoint_reveals_nothing_sensitive(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "database": "ok"}
    text = resp.get_data(as_text=True).lower()
    for word in ["sqlite", "mysql", "version", "password", "secret", "path"]:
        assert word not in text


def test_security_headers(client):
    resp = client.get("/login")
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Cache-Control"] == "no-store"
    assert "camera=(self)" in resp.headers["Permissions-Policy"]


def test_session_cookie_flags(browser, admin):
    resp = browser.login()
    cookie = resp.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_no_inline_scripts_in_templates(logged_in, make_worker):
    w = make_worker()
    for url in ["/", "/workers/", f"/workers/{w.id}", f"/workers/{w.id}/enroll", "/attendance/", "/kiosk/",
                "/audit/", "/reviews/", "/kiosk/devices"]:
        body = logged_in.get(url).get_data(as_text=True)
        assert "<script>" not in body and "onclick=" not in body, url


@pytest.mark.parametrize("target,expected", [
    ("/workers/", "/workers/"), ("https://evil.example", None), ("//evil.example", None),
    ("/\\evil.example", None), ("javascript:alert(1)", None), ("", None),
])
def test_safe_next_url(target, expected):
    assert safe_next_url(target) == expected


def test_image_validation_limits():
    limits = ImageLimits(max_bytes=200_000, min_dim=240, max_dim=1920, processing_max_width=640)
    with pytest.raises(ImageRejected):
        load_rgb(b"GIF89a....", limits)
    with pytest.raises(ImageRejected):
        load_rgb(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, limits)  # truncated PNG
    with pytest.raises(ImageRejected) as err:
        decode_data_url("data:image/svg+xml;base64,PHN2Zz4=", limits)
    assert err.value.code == "invalid_image"
    with pytest.raises(ImageRejected) as err:
        decode_data_url("data:image/png;base64," + "A" * 400_000, limits)
    assert err.value.code == "image_too_large"
    rgb = load_rgb(make_png(1, width=1280, height=720), ImageLimits(5_000_000, 240, 1920, 640))
    assert rgb.shape == (360, 640, 3)  # downscaled for processing


def test_config_validation_rejects_placeholders():
    cfg = {k: getattr(_TestingConfig(), k) for k in dir(_TestingConfig()) if k.isupper()}
    validate_config(cfg)
    for key, value in [("SECRET_KEY", "change-me"), ("SECRET_KEY", "short"), ("BUSINESS_TIMEZONE", "Mars/Base"),
                       ("FACE_MATCH_THRESHOLD", 1.5), ("FACE_DETECTION_MODEL", "magic"), ("SQLALCHEMY_DATABASE_URI", None),
                       ("LIVENESS_CHALLENGE_TYPES", ["smile"])]:
        bad = dict(cfg, **{key: value})
        with pytest.raises(ConfigError):
            validate_config(bad)


def test_payloads_and_encodings_not_logged(logged_in, make_worker, scene, caplog):
    w = make_worker()
    p = Person(1)
    images = [data_url(scene(FakeFace(p.sample()))) for _ in range(3)]
    with caplog.at_level(logging.DEBUG):
        logged_in.get(f"/workers/{w.id}/enroll")
        logged_in.post_json(f"/workers/{w.id}/enroll", {"images": images, "consent": True})
    for record in caplog.records:
        msg = record.getMessage()
        assert "base64" not in msg and images[0][30:80] not in msg
