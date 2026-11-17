"""Shared fixtures.

By default tests use a temporary SQLite file. Set TEST_DATABASE_URL to a MySQL URL
(a dedicated, disposable database; all tables are dropped) to run the whole suite plus
the MySQL-only concurrency tests against MySQL.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import pytest
import sqlalchemy as sa
from flask import g

from app import create_app
from app.extensions import db
from app.models import Admin, Worker
from app.services import workers as worker_service

from .fakes import FakeEngine, FakeFace, FrozenClock, Person, data_url, landmarks, make_png

ADMIN_PASSWORD = "correct-horse-battery-staple"
# 10:00 IST on 17 Nov 2026
DEFAULT_NOW = datetime(2026, 11, 17, 4, 30, 0)


def _database_url(tmp_path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"


def is_mysql() -> bool:
    return (os.environ.get("TEST_DATABASE_URL") or "").startswith("mysql")


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(DEFAULT_NOW)


@pytest.fixture
def app(engine, clock, tmp_path):
    application = create_app("testing", face_engine=engine, config_overrides={
        "SQLALCHEMY_DATABASE_URI": _database_url(tmp_path),
        "BIOMETRIC_STORAGE_DIR": str(tmp_path / "biometric"),
    })
    application.extensions["clock"] = clock

    # The fixture keeps one app context open for the whole test so test code can use
    # db.session directly. Flask reuses an already-pushed app context for test requests,
    # which would leak `g` (Flask-WTF's cached CSRF token, Flask-Login's user) between
    # requests. Reset `g` first thing in every request, as a fresh context would.
    def _fresh_g():
        g.__dict__.clear()
    application.before_request_funcs.setdefault(None, []).insert(0, _fresh_g)

    with application.app_context():
        _reset_schema()
        db.create_all()
        yield application
        db.session.remove()
        _reset_schema()
        db.engine.dispose()


def _reset_schema():
    db.session.remove()
    db.drop_all()
    with db.engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin(app) -> Admin:
    a = Admin(username="admin")
    a.set_password(ADMIN_PASSWORD)
    db.session.add(a)
    db.session.commit()
    return a


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', html) or re.search(
        r'name="csrf_token" type="hidden" value="([^"]+)"', html) or re.search(
        r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found in page"
    return match.group(1)


class Browser:
    """Small helper around the test client that tracks the CSRF token."""

    def __init__(self, client):
        self.client = client
        self.csrf = None

    def get(self, url, **kw):
        resp = self.client.get(url, **kw)
        if resp.mimetype == "text/html":
            try:
                self.csrf = csrf_from(resp.get_data(as_text=True))
            except AssertionError:
                pass
        return resp

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf_token", self.csrf)
        return self.client.post(url, data=data, **kw)

    def post_json(self, url, payload):
        return self.client.post(url, json=payload, headers={"X-CSRFToken": self.csrf})

    def login(self, username="admin", password=ADMIN_PASSWORD):
        self.get("/login")
        return self.post("/login", {"username": username, "password": password})


@pytest.fixture
def browser(client) -> Browser:
    return Browser(client)


@pytest.fixture
def logged_in(browser, admin) -> Browser:
    resp = browser.login()
    assert resp.status_code == 302
    browser.get("/")
    return browser


@pytest.fixture
def make_worker(app):
    counter = {"n": 0}

    def _make(code=None, name="Test Worker", department="Masonry", contractor="Acme", phone=None) -> Worker:
        counter["n"] += 1
        return worker_service.create_worker(worker_service.WorkerInput(
            code or f"W-{counter['n']:04d}", name, department, contractor, phone))
    return _make


@pytest.fixture
def scene(engine):
    """Register a frame and return its PNG bytes."""
    def _scene(*faces: FakeFace, **image_kw) -> bytes:
        return make_png(engine.register(list(faces)), **image_kw)
    return _scene


@pytest.fixture
def enroll_person(app, engine, scene, admin):
    """Directly enroll a synthetic person for a worker via the real enrollment service."""
    from app.services.enrollment import enroll

    def _enroll(worker: Worker, person: Person, n: int = 3):
        images = [scene(FakeFace(person.sample())) for _ in range(n)]
        return enroll(worker, images, consent_acknowledged=True, admin_id=admin.id)
    return _enroll


__all__ = ["FakeFace", "Person", "landmarks", "data_url", "make_png", "is_mysql"]
