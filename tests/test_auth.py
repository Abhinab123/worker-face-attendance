import sqlalchemy as sa

from app.extensions import db
from app.models import Admin, AuditEvent

from .conftest import ADMIN_PASSWORD


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Administrator sign in" in resp.data


def test_password_is_hashed_not_plaintext(admin):
    stored = db.session.get(Admin, admin.id).password_hash
    assert ADMIN_PASSWORD not in stored
    assert stored.startswith(("scrypt:", "pbkdf2:"))


def test_login_success_and_logout(browser, admin):
    resp = browser.login()
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    assert browser.get("/").status_code == 200
    resp = browser.post("/logout")
    assert resp.status_code == 302
    assert browser.get("/").status_code == 302  # protected again


def test_login_wrong_password_generic_message(browser, admin):
    resp = browser.login(password="wrong-password-123")
    assert resp.status_code == 401
    assert b"Invalid username or password." in resp.data
    resp = browser.login(username="nobody", password="whatever-123456")
    assert resp.status_code == 401
    assert b"Invalid username or password." in resp.data


def test_lockout_after_repeated_failures(browser, admin, app):
    for _ in range(app.config["LOGIN_MAX_ATTEMPTS"]):
        browser.login(password="bad-password-0000")
    # Even the correct password is refused while locked.
    resp = browser.login()
    assert resp.status_code == 429
    assert db.session.scalar(sa.select(AuditEvent).where(AuditEvent.action == "auth.lockout")) is not None


def test_login_requires_csrf(client, admin):
    resp = client.post("/login", data={"username": "admin", "password": ADMIN_PASSWORD})
    assert resp.status_code == 400


def test_logout_requires_post(logged_in):
    assert logged_in.client.get("/logout").status_code == 405


def test_open_redirect_blocked(browser, admin):
    browser.get("/login")
    resp = browser.post("/login?next=https://evil.example/", {"username": "admin", "password": ADMIN_PASSWORD})
    assert resp.headers["Location"] == "/"


def test_safe_next_redirect_allowed(browser, admin):
    browser.get("/login")
    resp = browser.post("/login?next=/workers/", {"username": "admin", "password": ADMIN_PASSWORD})
    assert resp.headers["Location"] == "/workers/"


def test_admin_routes_require_login(client):
    for url in ["/", "/workers/", "/workers/new", "/attendance/", "/attendance/export.csv", "/audit/",
                "/reviews/", "/kiosk/devices", "/attendance/new"]:
        resp = client.get(url)
        assert resp.status_code == 302, url
        assert "/login" in resp.headers["Location"], url


def test_disabled_admin_cannot_login(browser, admin):
    admin.is_enabled = False
    db.session.commit()
    assert browser.login().status_code == 401


def test_create_admin_cli(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-admin", "--username", "boss"], input="a-very-long-password\na-very-long-password\n")
    assert result.exit_code == 0, result.output
    created = db.session.scalar(sa.select(Admin).where(Admin.username == "boss"))
    assert created and created.check_password("a-very-long-password")
    short = runner.invoke(args=["create-admin", "--username", "boss2"], input="short\nshort\n")
    assert short.exit_code != 0
    dup = runner.invoke(args=["create-admin", "--username", "boss"], input="a-very-long-password\na-very-long-password\n")
    assert dup.exit_code != 0 and "already exists" in dup.output


def test_kiosk_device_session_cannot_reach_admin_pages(app, admin):
    from app.services.kiosk import create_device
    _device, token = create_device("Gate 1", admin_id=admin.id)
    kiosk_client = app.test_client()
    page = kiosk_client.get("/kiosk/activate").get_data(as_text=True)
    from .conftest import csrf_from
    resp = kiosk_client.post("/kiosk/activate", data={"token": token, "csrf_token": csrf_from(page)})
    assert resp.status_code == 302
    assert kiosk_client.get("/kiosk/").status_code == 200
    for url in ["/", "/workers/", "/attendance/export.csv", "/audit/"]:
        assert kiosk_client.get(url).status_code == 302, url
