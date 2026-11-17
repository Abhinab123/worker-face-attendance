"""Concurrency guarantees that depend on real database locking. MySQL only.

Run with:  TEST_DATABASE_URL=mysql+pymysql://user:pass@127.0.0.1:3306/worker_attendance_test pytest -m mysql
"""
import threading

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import AttendanceRecord, LivenessChallenge, Worker
from app.services import attendance, kiosk
from app.services.attendance import AttendanceError

from .conftest import is_mysql

pytestmark = [pytest.mark.mysql,
              pytest.mark.skipif(not is_mysql(), reason="set TEST_DATABASE_URL to a MySQL database")]

THREADS = 12


def _race(app, fn):
    barrier = threading.Barrier(THREADS)
    outcomes, errors = [], []

    def run():
        with app.app_context():
            try:
                barrier.wait()
                outcomes.append(fn())
            except AttendanceError as err:
                outcomes.append(err.code)
            except kiosk.ChallengeRejected as err:
                outcomes.append(err.code)
            except Exception as exc:  # surface unexpected failures
                errors.append(repr(exc))
            finally:
                db.session.remove()

    threads = [threading.Thread(target=run) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors
    return outcomes


def test_dialect_is_mysql(app):
    assert db.engine.dialect.name == "mysql"


def test_concurrent_check_ins_create_exactly_one_record(app, make_worker):
    worker_id = make_worker().id
    db.session.remove()

    def attempt():
        attendance.check_in(db.session.get(Worker, worker_id))
        return "ok"

    outcomes = _race(app, attempt)
    assert outcomes.count("ok") == 1
    assert outcomes.count("already_checked_in") == THREADS - 1
    assert db.session.scalar(sa.select(sa.func.count(AttendanceRecord.id))) == 1


def test_concurrent_check_outs_update_exactly_once(app, make_worker, clock):
    w = make_worker()
    attendance.check_in(w)
    clock.advance(hours=8)
    worker_id = w.id
    db.session.remove()

    def attempt():
        attendance.check_out(db.session.get(Worker, worker_id))
        return "ok"

    outcomes = _race(app, attempt)
    assert outcomes.count("ok") == 1
    assert outcomes.count("already_checked_out") == THREADS - 1


def test_concurrent_challenge_consumption_single_use(app):
    ctx = kiosk.KioskContext("session-abc", None, None)
    _challenge, token = kiosk.issue_challenge(ctx, "check_in")
    db.session.remove()

    def attempt():
        kiosk.consume_challenge(ctx, token, "check_in")
        return "ok"

    outcomes = _race(app, attempt)
    assert outcomes.count("ok") == 1
    assert outcomes.count("challenge_replayed") == THREADS - 1
    assert db.session.scalar(sa.select(LivenessChallenge.consumed_at)) is not None
