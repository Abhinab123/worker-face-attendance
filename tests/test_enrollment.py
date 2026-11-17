"""Enrollment validation and duplicate-identity review (mocked encodings)."""
import io
import os

import numpy as np
import pytest
import sqlalchemy as sa
from PIL import Image

from app.extensions import db
from app.face import codec
from app.models import AuditEvent, EnrollmentFlag, FaceEncoding, Worker
from app.services import enrollment as enrollment_service
from app.services.enrollment import EnrollmentError

from .fakes import FakeFace, Person, data_url, make_png


def _post(browser, worker, images, consent=True):
    browser.get(f"/workers/{worker.id}/enroll")
    return browser.post_json(f"/workers/{worker.id}/enroll",
                             {"images": [data_url(i) for i in images], "consent": consent})


def test_enroll_success_via_ui(logged_in, make_worker, scene):
    w = make_worker()
    p = Person(1)
    resp = _post(logged_in, w, [scene(FakeFace(p.sample())) for _ in range(3)])
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "enrolled" and body["stored"] == 3
    rows = db.session.scalars(sa.select(FaceEncoding).where(FaceEncoding.worker_id == w.id)).all()
    assert len(rows) == 3 and all(r.status == "active" and len(r.encoding) == 1024 for r in rows)
    assert all(r.image_path is None for r in rows)  # raw photos not retained by default
    db.session.expire_all()
    assert db.session.get(Worker, w.id).consent_given_at is not None


def test_enrollment_requires_consent(logged_in, make_worker, scene):
    w = make_worker()
    resp = _post(logged_in, w, [scene(FakeFace(Person(1).sample())) for _ in range(3)], consent=False)
    assert resp.status_code == 400 and "consent" in resp.get_json()["message"]
    assert db.session.scalar(sa.select(sa.func.count(FaceEncoding.id))) == 0


def test_enrollment_requires_admin(client, make_worker):
    w = make_worker()
    resp = client.post(f"/workers/{w.id}/enroll", json={"images": []})
    assert resp.status_code in (302, 400)  # redirect to login (or CSRF failure); never processed
    assert db.session.scalar(sa.select(sa.func.count(FaceEncoding.id))) == 0


def test_too_few_samples_rejected(logged_in, make_worker, scene):
    w = make_worker()
    resp = _post(logged_in, w, [scene(FakeFace(Person(1).sample()))])
    assert resp.status_code == 400 and "between" in resp.get_json()["message"]


def test_unusable_images_explained_and_nothing_saved(logged_in, make_worker, scene, engine):
    w = make_worker()
    p, q = Person(1), Person(2)
    images = [
        scene(FakeFace(p.sample())),                              # ok
        scene(FakeFace(p.sample()), FakeFace(q.sample())),        # two faces
        scene(),                                                  # no face
        scene(FakeFace(p.sample()), low=0, high=25),              # too dark
        scene(FakeFace(p.sample()), FakeFace(p.sample(), box=(0, 50, 40, 10))),  # still two faces
    ]
    resp = _post(logged_in, w, images)
    body = resp.get_json()
    assert resp.status_code == 422 and body["status"] == "rejected"
    codes = [s["code"] for s in body["samples"]]
    assert codes == ["ok", "multiple_faces", "no_face", "too_dark", "multiple_faces"]
    assert all(s["message"] for s in body["samples"])
    assert db.session.scalar(sa.select(sa.func.count(FaceEncoding.id))) == 0


def test_small_face_rejected(app, make_worker, scene, admin):
    w = make_worker()
    small = (100, 160, 150, 110)  # 50 px
    images = [scene(FakeFace(Person(1).sample(), box=small)) for _ in range(3)]
    result = enrollment_service.enroll(w, images, consent_acknowledged=True, admin_id=admin.id)
    assert result.status == "rejected"
    assert {s.code for s in result.samples} == {"face_too_small"}


def test_invalid_formats_rejected(logged_in, make_worker):
    w = make_worker()
    logged_in.get(f"/workers/{w.id}/enroll")
    gif = io.BytesIO()
    Image.new("RGB", (400, 400)).save(gif, format="GIF")
    for bad in ["not-a-data-url", "data:image/gif;base64," + __import__("base64").b64encode(gif.getvalue()).decode(),
                "data:image/png;base64,AAAA", 12345]:
        resp = logged_in.post_json(f"/workers/{w.id}/enroll", {"images": [bad, bad, bad], "consent": True})
        body = resp.get_json()
        assert resp.status_code in (400, 422), bad
        assert body["status"] == "rejected"


def test_image_dimension_limits(app, make_worker, scene, admin):
    w = make_worker()
    tiny = [scene(FakeFace(Person(1).sample()), width=200, height=150) for _ in range(3)]
    result = enrollment_service.enroll(w, tiny, consent_acknowledged=True, admin_id=admin.id)
    assert {s.code for s in result.samples} == {"image_too_small"}
    app.config["MAX_IMAGE_DIM"] = 400
    big = [scene(FakeFace(Person(1).sample()), width=480, height=360) for _ in range(3)]
    result = enrollment_service.enroll(w, big, consent_acknowledged=True, admin_id=admin.id)
    assert {s.code for s in result.samples} == {"image_too_large"}


def test_oversized_payload_rejected(logged_in, make_worker, app):
    w = make_worker()
    app.config["MAX_IMAGE_BYTES"] = 1000
    raw = make_png(1)
    assert len(raw) > 1000
    resp = _post(logged_in, w, [raw, raw, raw])
    assert resp.status_code == 400 and "too large" in resp.get_json()["message"]


def test_inconsistent_samples_rejected(app, make_worker, scene, admin):
    w = make_worker()
    images = [scene(FakeFace(Person(1).sample())), scene(FakeFace(Person(1).sample())),
              scene(FakeFace(Person(2).sample()))]
    result = enrollment_service.enroll(w, images, consent_acknowledged=True, admin_id=admin.id)
    assert result.status == "rejected" and "same person" in result.message
    assert db.session.scalar(sa.select(sa.func.count(FaceEncoding.id))) == 0


def test_inactive_worker_cannot_enroll(app, make_worker, scene, admin):
    from app.services.workers import set_status
    w = make_worker()
    set_status(w, False)
    with pytest.raises(EnrollmentError):
        enrollment_service.enroll(w, [scene(FakeFace(Person(1).sample())) for _ in range(3)],
                                  consent_acknowledged=True, admin_id=admin.id)


def test_same_face_under_other_id_is_flagged_not_merged(app, make_worker, enroll_person, admin):
    p = Person(7)
    first, second = make_worker("W-1"), make_worker("W-2")
    assert enroll_person(first, p).status == "enrolled"
    result = enroll_person(second, p)
    assert result.status == "pending_review" and result.flagged_worker_codes == ["W-1"]
    pending = db.session.scalars(sa.select(FaceEncoding).where(FaceEncoding.worker_id == second.id)).all()
    assert pending and all(e.status == "pending_review" for e in pending)
    flag = db.session.scalar(sa.select(EnrollmentFlag))
    assert flag.worker_id == second.id and flag.matched_worker_id == first.id and flag.status == "pending"
    # The original worker's samples are untouched; nothing was merged.
    assert all(e.status == "active" for e in db.session.scalars(
        sa.select(FaceEncoding).where(FaceEncoding.worker_id == first.id)))


def test_flag_against_inactive_worker(app, make_worker, enroll_person):
    from app.services.workers import set_status
    p = Person(8)
    old = make_worker("W-OLD")
    enroll_person(old, p)
    set_status(old, False)
    assert enroll_person(make_worker("W-NEW"), p).status == "pending_review"


def test_review_approve_activates(logged_in, make_worker, enroll_person):
    p = Person(9)
    enroll_person(make_worker("W-1"), p)
    w2 = make_worker("W-2")
    enroll_person(w2, p)
    flag = db.session.scalar(sa.select(EnrollmentFlag))
    logged_in.get("/reviews/")
    resp = logged_in.post(f"/reviews/{flag.id}/resolve", {"decision": "approve", "note": "Twins, verified IDs"})
    assert resp.status_code == 302
    db.session.expire_all()
    assert db.session.get(EnrollmentFlag, flag.id).status == "approved"
    assert all(e.status == "active" for e in db.session.scalars(
        sa.select(FaceEncoding).where(FaceEncoding.worker_id == w2.id)))


def test_review_reject_deletes_pending(logged_in, make_worker, enroll_person):
    p = Person(10)
    enroll_person(make_worker("W-1"), p)
    w2 = make_worker("W-2")
    enroll_person(w2, p)
    flag = db.session.scalar(sa.select(EnrollmentFlag))
    logged_in.get("/reviews/")
    logged_in.post(f"/reviews/{flag.id}/resolve", {"decision": "reject", "note": "Same person, duplicate ID"})
    db.session.expire_all()
    assert db.session.scalar(sa.select(sa.func.count(FaceEncoding.id)).where(FaceEncoding.worker_id == w2.id)) == 0
    assert db.session.scalar(sa.select(AuditEvent).where(AuditEvent.action == "enrollment.review_reject"))


def test_review_requires_note(logged_in, make_worker, enroll_person):
    p = Person(11)
    enroll_person(make_worker("W-1"), p)
    enroll_person(make_worker("W-2"), p)
    flag = db.session.scalar(sa.select(EnrollmentFlag))
    logged_in.get("/reviews/")
    logged_in.post(f"/reviews/{flag.id}/resolve", {"decision": "approve", "note": ""})
    db.session.expire_all()
    assert db.session.get(EnrollmentFlag, flag.id).status == "pending"


def test_delete_face_data(logged_in, make_worker, enroll_person):
    w = make_worker()
    enroll_person(w, Person(3))
    logged_in.get(f"/workers/{w.id}")
    resp = logged_in.post(f"/workers/{w.id}/face-data/delete", {"reason": "Worker request", "confirm": "y"})
    assert resp.status_code == 302
    assert db.session.scalar(sa.select(sa.func.count(FaceEncoding.id))) == 0
    event = db.session.scalar(sa.select(AuditEvent).where(AuditEvent.action == "enrollment.delete_face_data"))
    assert event.reason == "Worker request" and event.before_data == {"encodings": 3}


def test_purge_inactive_cli(app, make_worker, enroll_person, clock):
    from app.services.workers import set_status
    w = make_worker()
    enroll_person(w, Person(4))
    set_status(w, False)
    clock.advance(days=31)
    result = app.test_cli_runner().invoke(args=["biometrics", "purge-inactive"])
    assert result.exit_code == 0 and "1 workers matched; 3 samples deleted" in result.output
    assert db.session.scalar(sa.select(sa.func.count(FaceEncoding.id))) == 0


def test_store_images_only_when_configured_and_outside_static(app, make_worker, enroll_person, tmp_path):
    app.config["STORE_ENROLLMENT_IMAGES"] = True
    w = make_worker()
    enroll_person(w, Person(5))
    files = os.listdir(tmp_path / "biometric")
    assert len(files) == 3
    static_dir = os.path.join(app.root_path, "static")
    assert not str(tmp_path / "biometric").startswith(static_dir)
    assert (os.stat(tmp_path / "biometric" / files[0]).st_mode & 0o777) == 0o600
    enrollment_service.delete_face_data(w, reason="test cleanup")
    assert os.listdir(tmp_path / "biometric") == []


def test_codec_roundtrip_and_rejects_bad_blobs():
    v = np.random.default_rng(0).normal(size=128)
    assert np.allclose(codec.decode(codec.encode(v)), v)
    for bad in [b"", b"\x00" * 1023, b"\x80\x04\x95" + b"\x00" * 1021 + b"!"]:
        with pytest.raises(codec.EncodingFormatError):
            codec.decode(bad)
    with pytest.raises(codec.EncodingFormatError):
        codec.encode(np.zeros(64))
    with pytest.raises(codec.EncodingFormatError):
        codec.encode(np.full(128, np.nan))
