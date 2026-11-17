"""The Alembic migrations must build exactly the schema the models describe."""
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import downgrade, upgrade

from app.extensions import db


def test_upgrade_matches_models_and_downgrades(app):
    db.session.remove()
    db.drop_all()
    upgrade(directory="migrations")
    inspector = sa.inspect(db.engine)
    tables = set(inspector.get_table_names())
    assert {"admins", "workers", "face_encodings", "attendance_records", "audit_events", "enrollment_flags",
            "kiosk_devices", "liveness_challenges", "alembic_version"} <= tables
    uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("attendance_records")}
    indexes = {tuple(i["column_names"]) for i in inspector.get_indexes("attendance_records") if i.get("unique")}
    assert ("worker_id", "business_date") in uniques | indexes

    with db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), db.metadata)
    assert diff == [], diff

    downgrade(directory="migrations", revision="base")
    assert set(sa.inspect(db.engine).get_table_names()) <= {"alembic_version"}
