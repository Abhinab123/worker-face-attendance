# Database schema

MySQL 8.0 (InnoDB, `utf8mb4`). Managed by Alembic migrations in [`migrations/`](../migrations/versions/0001_initial_schema.py). All timestamps are **naive UTC** in `DATETIME(6)` (microsecond precision). They are converted to `BUSINESS_TIMEZONE` only for display and for computing the business date.

```mermaid
erDiagram
  admins ||--o{ workers : "records consent"
  admins ||--o{ face_encodings : "created_by"
  admins ||--o{ kiosk_devices : "created_by"
  admins ||--o{ audit_events : "actor"
  admins ||--o{ enrollment_flags : "resolved_by"
  workers ||--o{ face_encodings : has
  workers ||--o{ attendance_records : has
  workers ||--o{ enrollment_flags : "flagged (worker_id)"
  workers ||--o{ enrollment_flags : "resembles (matched_worker_id)"
  workers ||--o{ audit_events : about
  kiosk_devices ||--o{ attendance_records : "check-in/out device"
  kiosk_devices ||--o{ liveness_challenges : issued_to
  kiosk_devices ||--o{ audit_events : actor

  admins {
    int id PK
    varchar username UK
    varchar password_hash "scrypt"
    bool is_enabled
    int failed_login_count
    datetime locked_until
    datetime last_login_at
    datetime created_at
  }
  workers {
    int id PK
    varchar worker_code UK "e.g. W-1001"
    varchar full_name
    varchar department "indexed"
    varchar contractor "indexed"
    varchar phone "nullable"
    varchar status "active|inactive (CHECK)"
    datetime consent_given_at
    varchar consent_notice_version
    int consent_recorded_by_id FK
    datetime created_at
    datetime updated_at
    datetime deactivated_at
  }
  face_encodings {
    int id PK
    int worker_id FK "ON DELETE CASCADE"
    blob encoding "1024 bytes = 128 x float64 LE"
    varchar model_name
    varchar status "active|pending_review (CHECK)"
    int face_size_px
    float brightness
    float sharpness
    varchar image_path "only if STORE_ENROLLMENT_IMAGES"
    int created_by_id FK
    datetime created_at
  }
  enrollment_flags {
    int id PK
    int worker_id FK
    int matched_worker_id FK
    float distance
    varchar status "pending|approved|rejected"
    datetime created_at
    datetime resolved_at
    int resolved_by_id FK
    text resolution_note
  }
  kiosk_devices {
    int id PK
    varchar name
    char token_hash UK "sha256 of activation code"
    int created_by_id FK
    datetime created_at
    datetime activated_at
    datetime last_seen_at
    datetime revoked_at
  }
  liveness_challenges {
    int id PK
    char token_hash UK "sha256"
    varchar kiosk_session_id "indexed"
    int kiosk_device_id FK
    int admin_id FK
    varchar challenge_type "blink|turn_left|turn_right|none"
    varchar mode "check_in|check_out"
    datetime issued_at
    datetime expires_at "indexed"
    datetime consumed_at "single use"
    varchar outcome
  }
  attendance_records {
    int id PK
    int worker_id FK "ON DELETE RESTRICT"
    date business_date "indexed"
    datetime check_in_at
    datetime check_out_at
    varchar check_in_method "face|admin"
    varchar check_out_method "face|admin|NULL"
    float check_in_distance
    float check_out_distance
    int check_in_device_id FK
    int check_out_device_id FK
    bool is_corrected
    datetime created_at
    datetime updated_at
  }
  audit_events {
    int id PK
    datetime created_at "indexed"
    varchar actor_type "admin|kiosk|system"
    int actor_admin_id FK
    int kiosk_device_id FK
    varchar action "indexed, e.g. attendance.correct"
    varchar entity_type
    varchar entity_id
    int worker_id FK "indexed"
    text reason
    json before_data
    json after_data
    varchar ip_address
  }
```

## Constraints and indexes

| Table | Constraint / index | Purpose |
|---|---|---|
| `workers` | `uq_workers_worker_code` | Unique worker IDs (codes are upper-cased before insert) |
| `workers` | `ck_workers_status_valid` | Only `active`/`inactive` |
| `attendance_records` | **`uq_attendance_worker_date (worker_id, business_date)`** | One record per worker per business day; rejects concurrent duplicate check-ins |
| `attendance_records` | `ck_..._checkout_after_checkin` | `check_out_at IS NULL OR check_out_at >= check_in_at` (MySQL ≥ 8.0.16 enforces CHECK) |
| `attendance_records` | `ck_..._check_in_method_valid`, `..._check_out_method_valid` | Distinguish `face` from `admin` entries |
| `attendance_records` | FK `worker_id` **RESTRICT** | A worker with attendance history can't be deleted; they are deactivated instead |
| `face_encodings` | FK `worker_id` CASCADE, `ix_face_encodings_status_worker` | Fast load of active encodings |
| `liveness_challenges` | `uq_..._token_hash`, `ix_..._expires_at`, `ix_..._kiosk_session_id` | Lookup by hashed token and pruning |
| `kiosk_devices` | `uq_kiosk_devices_token_hash` | Activation by hashed code |
| `audit_events` | `ix_..._created_at`, `ix_..._action`, `ix_..._worker_id`, `ix_audit_events_entity` | Audit filtering |

## Duplicate prevention at the database level

- **Check-in** is a plain `INSERT`. If two kiosks recognise the same worker at the same moment, InnoDB serialises the two inserts on the unique key. The first commits and the second fails with error 1062 (duplicate key), which the service turns into "Already checked in today at HH:MM". No application-level lock is needed, and it holds across processes and servers.
- **Check-out** is one statement: `UPDATE attendance_records SET check_out_at=… WHERE worker_id=? AND business_date=? AND check_out_at IS NULL AND check_in_at <= ?`. Only the first concurrent request matches the row (`rowcount = 1`). The rest see `check_out_at` set and get "Already checked out".
- **Liveness challenges** are consumed with `UPDATE … SET consumed_at=now WHERE token_hash=? AND consumed_at IS NULL AND kiosk_session_id=? AND mode=? AND expires_at >= now`. A token works once, even under concurrent replay.

These guarantees are tested against MySQL in [`tests/test_mysql_concurrency.py`](../tests/test_mysql_concurrency.py), with 12 threads racing through a barrier.

## Serialization of encodings

`face_encodings.encoding` is exactly 1024 bytes: 128 little-endian IEEE-754 float64 values (`numpy.ndarray.tobytes()` with dtype `<f8`). Reading uses `numpy.frombuffer(blob, dtype='<f8')` after checking the length is 1024 and the values are finite. `pickle`, `np.load(allow_pickle=True)` or any other code-executing format is never used.

## Migrations

```bash
flask --app wsgi db upgrade                 # apply
flask --app wsgi db downgrade               # step back one revision
flask --app wsgi db migrate -m "message"    # autogenerate after model changes, then REVIEW the file
flask --app wsgi db current                 # show applied revision
```

`tests/test_migrations.py` applies all migrations to an empty database, checks with Alembic's `compare_metadata` that the result matches the models exactly, then downgrades to base.

## Backups

Database dumps contain biometric templates and personal data. Store them encrypted and with access control, and never in the repository (`*.sql`, `*.dump` are git-ignored). Example: `docker exec wfa-mysql mysqldump -uroot -p --single-transaction worker_attendance | gpg -c > backup.sql.gpg`.
