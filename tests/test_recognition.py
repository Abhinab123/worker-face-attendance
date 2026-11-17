"""Matching logic with mocked encodings. Not a measure of real-world accuracy."""
import numpy as np
import sqlalchemy as sa

from app.extensions import db
from app.models import FaceEncoding
from app.services import recognition
from app.services.workers import set_status

from .fakes import Person


def test_decide_rules():
    ids = np.array([1, 2, 3])
    assert recognition.decide(ids, np.array([0.30, 0.80, 0.90]), 0.5, 0.06).status == "match"
    assert recognition.decide(ids, np.array([0.30, 0.80, 0.90]), 0.5, 0.06).worker_id == 1
    assert recognition.decide(ids, np.array([0.55, 0.80, 0.90]), 0.5, 0.06).status == "unknown"
    # Two workers both close and within the margin: refuse.
    assert recognition.decide(ids, np.array([0.40, 0.43, 0.90]), 0.5, 0.06).status == "ambiguous"
    # Runner-up close to threshold but clearly separated from best: accept.
    assert recognition.decide(ids, np.array([0.30, 0.52, 0.90]), 0.5, 0.06).status == "match"
    assert recognition.decide(np.array([], dtype=int), np.array([]), 0.5, 0.06).status == "no_enrollments"


def test_known_unknown_and_ambiguous(app, make_worker, enroll_person):
    asha, ravi = Person(1), Person(2)
    w1, w2 = make_worker("W-1"), make_worker("W-2")
    enroll_person(w1, asha)
    enroll_person(w2, ravi)
    assert recognition.match_encoding(asha.sample()).worker_id == w1.id
    assert recognition.match_encoding(ravi.sample()).worker_id == w2.id
    assert recognition.match_encoding(Person(99).sample()).status == "unknown"
    # A probe exactly halfway between two enrolled people is ambiguous when both are under threshold.
    app.config["FACE_MATCH_THRESHOLD"] = 0.99
    midpoint = (asha.base + ravi.base) / 2
    assert recognition.match_encoding(midpoint).status == "ambiguous"


def test_inactive_worker_excluded_and_cache_invalidated(app, make_worker, enroll_person):
    p = Person(3)
    w = make_worker()
    enroll_person(w, p)
    assert recognition.match_encoding(p.sample()).status == "match"
    set_status(w, False)
    assert recognition.match_encoding(p.sample()).status == "no_enrollments"
    set_status(w, True)
    assert recognition.match_encoding(p.sample()).status == "match"


def test_cache_detects_out_of_band_changes(app, make_worker, enroll_person):
    p = Person(4)
    w = make_worker()
    enroll_person(w, p)
    assert recognition.match_encoding(p.sample()).status == "match"
    # Simulate another process deleting encodings without calling invalidate().
    db.session.execute(sa.delete(FaceEncoding))
    db.session.commit()
    assert recognition.match_encoding(p.sample()).status == "no_enrollments"


def test_pending_encodings_not_used_for_matching(app, make_worker, enroll_person):
    p = Person(5)
    enroll_person(make_worker("W-1"), p)
    w2 = make_worker("W-2")
    enroll_person(w2, p)  # pending review
    result = recognition.match_encoding(p.sample())
    assert result.status == "match" and result.worker_id != w2.id


def test_sequence_requires_consistent_identity(app, make_worker, enroll_person):
    a, b = Person(6), Person(7)
    w1, w2 = make_worker("W-1"), make_worker("W-2")
    enroll_person(w1, a)
    enroll_person(w2, b)
    assert recognition.match_sequence([a.sample() for _ in range(5)]).worker_id == w1.id
    mixed = [a.sample() for _ in range(4)] + [b.sample()]
    assert recognition.match_sequence(mixed).status == "ambiguous"
    mostly_unknown = [a.sample()] + [Person(50 + i).sample() for i in range(4)]
    assert recognition.match_sequence(mostly_unknown).status == "unknown"
