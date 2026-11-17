import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import AttendanceRecord, AuditEvent, Worker
from app.services import attendance
from app.services import workers as worker_service
from app.services.workers import WorkerError, WorkerInput


def _form(**kw):
    data = {"worker_code": "W-100", "full_name": "Lakshmi Test", "department": "Masonry",
            "contractor": "Acme", "phone": ""}
    data.update(kw)
    return data


def test_register_worker_via_ui(logged_in):
    logged_in.get("/workers/new")
    resp = logged_in.post("/workers/new", _form(worker_code="w-100"))
    assert resp.status_code == 302 and "/enroll" in resp.headers["Location"]
    worker = db.session.scalar(sa.select(Worker).where(Worker.worker_code == "W-100"))
    assert worker is not None and worker.status == "active"  # code normalised to upper case
    assert db.session.scalar(sa.select(AuditEvent).where(AuditEvent.action == "worker.create"))


def test_duplicate_worker_id_rejected(logged_in, make_worker):
    make_worker("W-100")
    logged_in.get("/workers/new")
    resp = logged_in.post("/workers/new", _form(worker_code="w-100"))
    assert resp.status_code == 400
    assert b"already exists" in resp.data
    assert db.session.scalar(sa.select(sa.func.count(Worker.id))) == 1


def test_duplicate_worker_id_db_constraint(app, make_worker):
    make_worker("W-1")
    with pytest.raises(sa.exc.IntegrityError):
        db.session.add(Worker(worker_code="W-1", full_name="X Y", department="D", contractor="C"))
        db.session.flush()
    db.session.rollback()


@pytest.mark.parametrize("field,value", [
    ("worker_code", "!bad"), ("worker_code", "A"), ("worker_code", "x" * 40),
    ("full_name", "A"), ("department", ""), ("contractor", ""), ("phone", "12ab"),
])
def test_worker_validation(app, field, value):
    data = WorkerInput("W-9", "Valid Name", "Dept", "Contractor", None)
    setattr(data, field, value)
    with pytest.raises(WorkerError) as err:
        worker_service.create_worker(data)
    assert err.value.field == field


def test_edit_worker_and_audit(logged_in, make_worker):
    w = make_worker("W-5")
    logged_in.get(f"/workers/{w.id}/edit")
    resp = logged_in.post(f"/workers/{w.id}/edit", _form(worker_code="W-5", full_name="New Name", phone="+91 98765 43210"))
    assert resp.status_code == 302
    db.session.expire_all()
    assert db.session.get(Worker, w.id).full_name == "New Name"
    event = db.session.scalar(sa.select(AuditEvent).where(AuditEvent.action == "worker.update"))
    assert event.before_data["full_name"] == "Test Worker" and event.after_data["full_name"] == "New Name"


def test_edit_to_existing_code_rejected(logged_in, make_worker):
    make_worker("W-1")
    w2 = make_worker("W-2")
    logged_in.get(f"/workers/{w2.id}/edit")
    resp = logged_in.post(f"/workers/{w2.id}/edit", _form(worker_code="W-1"))
    assert resp.status_code == 400 and b"already exists" in resp.data


def test_deactivate_keeps_attendance_history(logged_in, make_worker, clock):
    w = make_worker("W-7")
    attendance.check_in(w, method="face", distance=0.3)
    page = logged_in.get(f"/workers/{w.id}")
    assert page.status_code == 200
    resp = logged_in.post(f"/workers/{w.id}/status", {"action": "deactivate", "reason": "Contract ended"})
    assert resp.status_code == 302
    db.session.expire_all()
    worker = db.session.get(Worker, w.id)
    assert worker.status == "inactive" and worker.deactivated_at is not None
    assert db.session.scalar(sa.select(sa.func.count(AttendanceRecord.id))) == 1
    # Reactivate
    logged_in.post(f"/workers/{w.id}/status", {"action": "activate"})
    db.session.expire_all()
    assert db.session.get(Worker, w.id).status == "active"


def test_search_and_filters(logged_in, make_worker):
    make_worker("W-1", name="Asha Kumar", department="Masonry", contractor="Acme")
    make_worker("W-2", name="Ravi Singh", department="Electrical", contractor="Bright")
    body = logged_in.get("/workers/?q=asha").get_data(as_text=True)
    assert "Asha Kumar" in body and "Ravi Singh" not in body
    body = logged_in.get("/workers/?department=Electrical").get_data(as_text=True)
    assert "Ravi Singh" in body and "Asha Kumar" not in body
    body = logged_in.get("/workers/?q=%25").get_data(as_text=True)  # LIKE wildcard is escaped
    assert "No workers match" in body


def test_seed_demo_is_fictional_and_idempotent(app):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-demo"]).exit_code == 0
    assert runner.invoke(args=["seed-demo"]).exit_code == 0
    codes = list(db.session.scalars(sa.select(Worker.worker_code)))
    assert len(codes) == 6 and all(c.startswith("DEMO-") for c in codes)
