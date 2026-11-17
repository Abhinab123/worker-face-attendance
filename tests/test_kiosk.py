"""End-to-end kiosk flow through HTTP with a fake face engine (mocked encodings/landmarks)."""
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import AttendanceRecord, AuditEvent, LivenessChallenge
from app.services import kiosk as kiosk_service
from app.services.workers import set_status

from .conftest import csrf_from
from .fakes import FakeFace, Person, data_url, landmarks


def blink_frames(scene, person, n=10):
    ears = [0.30, 0.31, 0.30, 0.12, 0.10, 0.29, 0.31, 0.30, 0.30, 0.31][:n]
    return [data_url(scene(FakeFace(person.sample(), landmarks(ear=e)))) for e in ears]


def static_frames(scene, person, n=10):
    return [data_url(scene(FakeFace(person.sample(), landmarks(ear=0.30)))) for _ in range(n)]


def turn_frames(scene, person, direction):
    sign = 1 if direction == "turn_left" else -1
    yaws = [0.5, 0.5, 0.52, 0.5 + sign * 0.1, 0.5 + sign * 0.2, 0.5 + sign * 0.22, 0.5 + sign * 0.1, 0.5]
    return [data_url(scene(FakeFace(person.sample(), landmarks(yaw=y)))) for y in yaws]


def frames_for(challenge_type, scene, person):
    if challenge_type == "blink":
        return blink_frames(scene, person)
    return turn_frames(scene, person, challenge_type)


@pytest.fixture
def kiosk(logged_in):
    logged_in.get("/kiosk/")

    def challenge(mode="check_in"):
        resp = logged_in.post_json("/kiosk/api/challenge", {"mode": mode})
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()

    def verify(token, mode, frames):
        return logged_in.post_json("/kiosk/api/verify", {"token": token, "mode": mode, "frames": frames}).get_json()

    logged_in.challenge, logged_in.verify = challenge, verify
    return logged_in


@pytest.fixture
def enrolled(make_worker, enroll_person):
    person = Person(42)
    worker = make_worker("W-42", name="Kiosk Tester")
    enroll_person(worker, person)
    return worker, person


@pytest.mark.parametrize("challenge_type", ["blink", "turn_left", "turn_right"])
def test_check_in_then_check_out(kiosk, enrolled, scene, app, challenge_type, clock):
    app.config["LIVENESS_CHALLENGE_TYPES"] = [challenge_type]
    worker, person = enrolled
    ch = kiosk.challenge("check_in")
    assert ch["challenge_type"] == challenge_type and ch["instruction"]
    result = kiosk.verify(ch["token"], "check_in", frames_for(challenge_type, scene, person))
    assert result["ok"] and result["status"] == "recorded", result
    assert result["worker"]["worker_code"] == "W-42"
    record = db.session.scalar(sa.select(AttendanceRecord))
    assert record.check_in_method == "face" and record.check_in_distance < 0.5

    clock.advance(hours=8)
    ch = kiosk.challenge("check_out")
    result = kiosk.verify(ch["token"], "check_out", frames_for(challenge_type, scene, person))
    assert result["status"] == "recorded", result
    db.session.expire_all()
    assert db.session.scalar(sa.select(AttendanceRecord)).check_out_method == "face"


def test_turn_wrong_direction_fails(kiosk, enrolled, scene, app):
    app.config["LIVENESS_CHALLENGE_TYPES"] = ["turn_left"]
    _w, person = enrolled
    ch = kiosk.challenge()
    result = kiosk.verify(ch["token"], "check_in", turn_frames(scene, person, "turn_right"))
    assert result["status"] == "liveness_failed"
    assert db.session.scalar(sa.select(sa.func.count(AttendanceRecord.id))) == 0


def test_static_photo_fails_blink_challenge(kiosk, enrolled, scene, app):
    """A printed photo shows no blink: rejected. (A video replay could still pass; see docs.)"""
    app.config["LIVENESS_CHALLENGE_TYPES"] = ["blink"]
    _w, person = enrolled
    ch = kiosk.challenge()
    result = kiosk.verify(ch["token"], "check_in", static_frames(scene, person))
    assert result["status"] == "liveness_failed" and not result["ok"]


def test_unknown_face(kiosk, enrolled, scene, app):
    app.config["LIVENESS_CHALLENGE_TYPES"] = ["blink"]
    ch = kiosk.challenge()
    result = kiosk.verify(ch["token"], "check_in", blink_frames(scene, Person(1234)))
    assert result["status"] == "unknown_face" and result["worker"] is None
    assert db.session.scalar(sa.select(AuditEvent).where(AuditEvent.action == "kiosk.attempt_rejected"))


def test_multiple_faces(kiosk, enrolled, scene):
    _w, person = enrolled
    ch = kiosk.challenge()
    frames = [data_url(scene(FakeFace(person.sample(), landmarks()), FakeFace(Person(5).sample(), landmarks())))] * 8
    assert kiosk.verify(ch["token"], "check_in", frames)["status"] == "multiple_faces"


def test_poor_lighting(kiosk, enrolled, scene):
    _w, person = enrolled
    ch = kiosk.challenge()
    frames = [data_url(scene(FakeFace(person.sample(), landmarks()), low=0, high=20)) for _ in range(8)]
    result = kiosk.verify(ch["token"], "check_in", frames)
    assert result["status"] == "poor_lighting" and any("dark" in h.lower() for h in result["hints"])


def test_ambiguous_match_rejected(kiosk, make_worker, enroll_person, scene, app):
    app.config["LIVENESS_CHALLENGE_TYPES"] = ["blink"]
    app.config["FACE_MATCH_THRESHOLD"] = 0.99
    a, b = Person(1), Person(2)
    enroll_person(make_worker("W-A"), a)
    enroll_person(make_worker("W-B"), b)

    class Mid:
        def sample(self):
            return (a.base + b.base) / 2
    ch = kiosk.challenge()
    result = kiosk.verify(ch["token"], "check_in", blink_frames(scene, Mid()))
    assert result["status"] == "ambiguous"
    assert db.session.scalar(sa.select(sa.func.count(AttendanceRecord.id))) == 0


def test_inactive_worker_rejected(kiosk, enrolled, scene, app):
    app.config["LIVENESS_CHALLENGE_TYPES"] = ["blink"]
    worker, person = enrolled
    set_status(worker, False)
    ch = kiosk.challenge()
    result = kiosk.verify(ch["token"], "check_in", blink_frames(scene, person))
    assert not result["ok"] and result["status"] in {"no_enrollments", "unknown_face"}
    assert db.session.scalar(sa.select(sa.func.count(AttendanceRecord.id))) == 0


def test_duplicate_check_in_via_kiosk(kiosk, enrolled, scene, app):
    app.config["LIVENESS_CHALLENGE_TYPES"] = ["blink"]
    _w, person = enrolled
    for expected in ["recorded", "already_checked_in"]:
        ch = kiosk.challenge()
        assert kiosk.verify(ch["token"], "check_in", blink_frames(scene, person))["status"] == expected
    assert db.session.scalar(sa.select(sa.func.count(AttendanceRecord.id))) == 1


def test_cooldown_suppresses_repeat_notification(kiosk, enrolled, scene, app):
    app.config["LIVENESS_CHALLENGE_TYPES"] = ["blink"]
    app.config["RECOGNITION_COOLDOWN_SECONDS"] = 60
    _w, person = enrolled
    ch = kiosk.challenge()
    assert kiosk.verify(ch["token"], "check_in", blink_frames(scene, person))["status"] == "recorded"
    ch = kiosk.challenge()
    again = kiosk.verify(ch["token"], "check_in", blink_frames(scene, person))
    assert again["status"] == "cooldown" and again["ok"]


def test_check_out_before_check_in_via_kiosk(kiosk, enrolled, scene, app):
    app.config["LIVENESS_CHALLENGE_TYPES"] = ["blink"]
    _w, person = enrolled
    ch = kiosk.challenge("check_out")
    assert kiosk.verify(ch["token"], "check_out", blink_frames(scene, person))["status"] == "not_checked_in"


def test_challenge_replay_rejected(kiosk, enrolled, scene, app):
    app.config["LIVENESS_CHALLENGE_TYPES"] = ["blink"]
    _w, person = enrolled
    ch = kiosk.challenge()
    frames = blink_frames(scene, person)
    assert kiosk.verify(ch["token"], "check_in", frames)["status"] == "recorded"
    assert kiosk.verify(ch["token"], "check_in", frames)["status"] == "challenge_replayed"


def test_challenge_replay_rejected_even_after_failure(kiosk, enrolled, scene):
    _w, person = enrolled
    ch = kiosk.challenge()
    kiosk.verify(ch["token"], "check_in", ["garbage"])
    assert kiosk.verify(ch["token"], "check_in", blink_frames(scene, person))["status"] == "challenge_replayed"


def test_challenge_expiry(kiosk, enrolled, scene, clock, app):
    _w, person = enrolled
    ch = kiosk.challenge()
    clock.advance(seconds=app.config["LIVENESS_CHALLENGE_TTL_SECONDS"] + 1)
    assert kiosk.verify(ch["token"], "check_in", blink_frames(scene, person))["status"] == "challenge_expired"


def test_challenge_bound_to_session_and_mode(kiosk, enrolled, scene, app, admin):
    _w, person = enrolled
    ch = kiosk.challenge("check_in")
    # Wrong mode for this token
    assert kiosk.verify(ch["token"], "check_out", blink_frames(scene, person))["status"] == "challenge_invalid"
    # Another kiosk session (different browser) cannot use the token.
    ch2 = kiosk.challenge("check_in")
    other = app.test_client()
    page = other.get("/login").get_data(as_text=True)
    other.post("/login", data={"username": "admin", "password": "correct-horse-battery-staple",
                               "csrf_token": csrf_from(page)})
    html = other.get("/kiosk/").get_data(as_text=True)
    resp = other.post("/kiosk/api/verify", json={"token": ch2["token"], "mode": "check_in",
                                                 "frames": blink_frames(scene, person)},
                      headers={"X-CSRFToken": csrf_from(html)})
    assert resp.get_json()["status"] == "challenge_invalid"


def test_challenge_token_stored_hashed(kiosk):
    ch = kiosk.challenge()
    row = db.session.scalar(sa.select(LivenessChallenge))
    assert row.token_hash != ch["token"] and len(row.token_hash) == 64


def test_kiosk_api_requires_authorised_session(client):
    resp = client.post("/kiosk/api/challenge", json={"mode": "check_in"})
    assert resp.status_code in (400, 401)  # CSRF or unauthorised; never issues a challenge
    assert client.get("/kiosk/").status_code == 302


def test_kiosk_device_activation_and_revocation(app, admin, enrolled, scene):
    app.config["LIVENESS_CHALLENGE_TYPES"] = ["blink"]
    device, token = kiosk_service.create_device("Gate 1 tablet", admin.id)
    c = app.test_client()
    page = c.get("/kiosk/activate").get_data(as_text=True)
    bad = c.post("/kiosk/activate", data={"token": "wrong", "csrf_token": csrf_from(page)})
    assert bad.status_code == 400
    assert c.post("/kiosk/activate", data={"token": token, "csrf_token": csrf_from(page)}).status_code == 302
    html = c.get("/kiosk/").get_data(as_text=True)
    assert "Gate 1 tablet" in html
    headers = {"X-CSRFToken": csrf_from(html)}
    ch = c.post("/kiosk/api/challenge", json={"mode": "check_in"}, headers=headers).get_json()
    _w, person = enrolled
    res = c.post("/kiosk/api/verify", json={"token": ch["token"], "mode": "check_in",
                                            "frames": blink_frames(scene, person)}, headers=headers).get_json()
    assert res["status"] == "recorded"
    assert db.session.scalar(sa.select(AttendanceRecord)).check_in_device_id == device.id

    kiosk_service.revoke_device(device, admin.id)
    resp = c.post("/kiosk/api/challenge", json={"mode": "check_in"}, headers=headers)
    assert resp.status_code == 401


def test_prune_expired_challenges(kiosk, clock, app):
    kiosk.challenge()
    clock.advance(days=2)
    assert kiosk_service.prune_challenges(24) == 1
