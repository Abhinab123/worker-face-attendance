# Worker Face Attendance

A facial-recognition attendance system for daily-wage workers. Workers check in and out at a browser-based kiosk. The server identifies enrolled, active workers from their faces and records one attendance record per worker per day. The goal is fewer manual registers, shorter queues, no duplicate entries, and fewer chances for proxy ("buddy") attendance.

> **Proxy attendance is reduced, not eliminated.** Basic face recognition with a webcam blink or head-turn challenge stops casual misuse, such as holding up a printed photo. It **can be defeated** by a video replay on a phone or tablet, a good mask, or a cooperating person. See [Limits of liveness](docs/recognition.md#limits-of-liveness-and-proxy-protection). Treat it as a deterrent and keep human supervision at the kiosk.

## Scope

Version 1 is intended for a single site with one or more kiosk devices and a small admin team. It covers worker registration, consented face enrollment, kiosk check-in and check-out, administrator corrections with an audit trail, reports, and CSV export. It does not cover rosters, shift scheduling, payroll, overnight shifts, or production-grade anti-spoofing.

## Implemented features

| Area | What exists |
|---|---|
| Admin auth | Login/logout, scrypt password hashes, CSRF on every form and JSON call, account lockout after repeated failures, `flask create-admin` CLI |
| Workers | Add, view, edit, deactivate/reactivate, search and filter. Unique worker IDs, enforced in the service and by a DB `UNIQUE` constraint. Deactivation keeps history |
| Enrollment | Browser webcam captures 3–8 samples. Each sample must show exactly one face, with checks for brightness, sharpness and face size, plus specific guidance when a sample is rejected. A consent notice and acknowledgment are required. A self-consistency check rejects sample sets that mix different people |
| Duplicate identity | New samples that resemble a *different* worker are stored as `pending_review` and flagged for an administrator. They are never merged automatically or used for matching until approved |
| Kiosk | Browser camera with permission and error guidance. A server-issued, single-use, 30 s liveness challenge (blink / turn left / turn right) tied to the kiosk session. Handles unknown, multiple-face, poor-lighting and ambiguous cases, with a cooldown |
| Kiosk access | Either a signed-in administrator, or a browser explicitly enrolled as a kiosk device with a one-time activation code (stored hashed, revocable) |
| Attendance rules | Explicit check-in/check-out modes. `UNIQUE(worker_id, business_date)`. Check-out is an atomic conditional UPDATE. Duplicate check-ins, duplicate check-outs and check-out before check-in are rejected. Business date and display use `Asia/Kolkata` by default (configurable). Overnight shifts are rejected with a clear message |
| Reports | Dashboard (active, enrolled, checked in, checked out, still on site, missing check-outs, pending reviews, recent activity). History filtered by date range, worker, department and contractor. CSV export protected against formula injection |
| Corrections | Admin edit or manual record, reason required. Rows are labelled **Admin entry**, separate from **Face (kiosk)**. Before/after values, actor and time are written to the audit trail |
| Privacy | Raw photos are **not** stored by default. Encodings are raw float64 bytes (no pickle). Biometric storage lives outside `static/`. Face data can be deleted per worker, and a retention purge job is provided |
| Ops | Health endpoint, Alembic migrations, Docker Compose for MySQL, OpenCV camera and engine diagnostics, loopback-only dev server |

## Technology stack

Python 3.10 · Flask 3.0 · Flask-Login · Flask-WTF (CSRF) · SQLAlchemy 2.0 · Flask-Migrate/Alembic · MySQL 8 (PyMySQL) · **face_recognition 1.3.0 / dlib 19.24.6** · OpenCV 4.10 (headless) · Pillow · NumPy · Bootstrap 5.3 (vendored, no CDN) · vanilla JavaScript · pytest.

## Directory structure

```
worker-face-attendance/
├── app/
│   ├── __init__.py          # application factory, security headers, error handlers
│   ├── config.py            # Development/Production/Testing config classes + validation
│   ├── extensions.py        # db, migrate, login manager, CSRF
│   ├── models.py            # SQLAlchemy models (schema: docs/database.md)
│   ├── timeutil.py          # UTC storage, business-timezone conversion
│   ├── cli.py               # create-admin, seed-demo, biometrics, liveness, face diagnostics
│   ├── forms.py, security.py
│   ├── face/                # engine (dlib wrapper), imaging (validation/quality), liveness, codec
│   ├── services/            # workers, enrollment, recognition, attendance, kiosk, reports, audit
│   ├── blueprints/          # auth, main, workers, reviews, attendance, audit_log, kiosk
│   ├── templates/           # Jinja2 + Bootstrap
│   └── static/              # css, js (camera, enroll, kiosk), vendor/bootstrap
├── migrations/              # Alembic (Flask-Migrate)
├── tests/                   # pytest; fakes.py provides a mocked face engine
├── docs/                    # architecture, database, recognition, testing, security, troubleshooting
├── scripts/mysql-init/      # creates the MySQL test database on first container start
├── docker-compose.yml       # MySQL 8 bound to 127.0.0.1
├── requirements.txt         # pinned runtime deps
├── requirements-dev.txt     # + pytest
├── .env.example             # configuration template (copy to .env)
└── wsgi.py                  # entry point
```

## Prerequisites

- **Python 3.10–3.12** (tested on 3.10.12).
- **A C++ toolchain and CMake 3.x** to compile dlib: `build-essential cmake` on Ubuntu, Xcode Command Line Tools + `brew install cmake` on macOS, Visual Studio Build Tools on Windows (see below). Compiling takes about 4 minutes on a fast machine and can take 15+ minutes on a laptop.
- **Docker** with Compose v2 for MySQL (or any MySQL 8.0 server).
- A webcam and a current Chrome, Edge or Firefox. Browsers only allow camera access on `http://localhost` / `127.0.0.1` or over HTTPS.

### Windows prerequisites for dlib / face_recognition

There are no official dlib wheels on PyPI, so pip compiles it:

1. Install **Visual Studio 2022 Build Tools** with the **"Desktop development with C++"** workload (MSVC v143 + Windows SDK).
2. Install **CMake** (`winget install Kitware.CMake`) and make sure `cmake --version` works in a *new* PowerShell window.
3. Use 64-bit Python from python.org (not the Microsoft Store build).
4. Install dlib **before** the rest: `pip install dlib==19.24.6`, then `pip install -r requirements.txt`.

If the build still fails, `conda install -c conda-forge dlib=19.24` provides a prebuilt dlib. It is the same library, so `face_recognition` works unchanged. More fixes are in [docs/troubleshooting.md](docs/troubleshooting.md).

## Installation

### Linux / macOS

```bash
git clone <your-repo-url> worker-face-attendance && cd worker-face-attendance
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade "pip==24.3.1" "setuptools==75.6.0" "wheel==0.45.1"
pip install dlib==19.24.6            # compiles; takes a few minutes
pip install -r requirements-dev.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste into SECRET_KEY in .env
# Edit .env: set MYSQL_ROOT_PASSWORD, MYSQL_PASSWORD, and the same password inside DATABASE_URL.
```

### Windows PowerShell

```powershell
git clone <your-repo-url> worker-face-attendance; cd worker-face-attendance
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade "pip==24.3.1" "setuptools==75.6.0" "wheel==0.45.1"
pip install dlib==19.24.6
pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste into SECRET_KEY in .env
notepad .env
```

## Environment configuration

All settings come from environment variables, loaded from `.env`. `.env` is git-ignored and must never be committed. The app refuses to start if `SECRET_KEY` is missing, a placeholder, or shorter than 32 characters.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `development` / `production` (forces secure cookies) |
| `SECRET_KEY` | none (required) | Session signing key |
| `DATABASE_URL` | none (required) | `mysql+pymysql://user:pass@127.0.0.1:3306/worker_attendance?charset=utf8mb4` |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Dev server bind (loopback by default) |
| `BUSINESS_TIMEZONE` | `Asia/Kolkata` | Business date and display timezone |
| `FACE_MATCH_THRESHOLD` | `0.50` | Max face distance to accept a match (lower = stricter) |
| `FACE_AMBIGUITY_MARGIN` | `0.06` | Reject if the 2nd-best worker is within this distance of the best |
| `ENROLL_DUPLICATE_THRESHOLD` | `0.45` | Distance below which a new enrollment is flagged as possibly another worker |
| `ENROLL_MIN_SAMPLES` / `ENROLL_MAX_SAMPLES` | `3` / `8` | Samples per enrollment submission |
| `FACE_DETECTION_MODEL` | `hog` | `hog` (CPU) or `cnn` (needs CUDA-enabled dlib) |
| `RECOGNITION_COOLDOWN_SECONDS` | `20` | Suppress repeat notifications for the same worker/mode |
| `LIVENESS_REQUIRED` | `true` | Blink/head-turn challenge at the kiosk |
| `LIVENESS_CHALLENGE_TTL_SECONDS` | `30` | Challenge lifetime |
| `STORE_ENROLLMENT_IMAGES` | `false` | Keep raw enrollment JPEGs (in `instance/biometric/`, mode 0600) |
| `BIOMETRIC_RETENTION_DAYS` | `30` | Purge face data this many days after deactivation (via CLI job) |

Full list with all tuning knobs: [app/config.py](app/config.py).

## Database setup and migrations

```bash
docker compose --env-file .env up -d          # MySQL 8.0 on 127.0.0.1:3306
docker compose ps                             # wait until "healthy"
flask --app wsgi db upgrade                   # apply migrations
```

The first container start also creates `worker_attendance_test` for the MySQL test suite (see [scripts/mysql-init](scripts/mysql-init/01-test-database.sh)). After changing models, run `flask --app wsgi db migrate -m "describe change"`, review the generated file, then `flask --app wsgi db upgrade`.

## Create the first administrator

```bash
flask --app wsgi create-admin --username admin     # prompts for the password twice (min 12 chars)
flask --app wsgi seed-demo                          # optional: 6 fictional workers, metadata only
```

## Run on localhost

```bash
flask --app wsgi run            # http://127.0.0.1:5000
# or: python wsgi.py            # honours HOST/PORT from .env
```

Open <http://127.0.0.1:5000/login>. `GET /healthz` returns `{"status":"ok","database":"ok"}` (or HTTP 503 if the database is unreachable). The Flask server is for development only. For anything beyond one machine, see [deployment notes](docs/security-and-privacy.md#non-local-deployment).

## Walkthrough: enrollment and attendance

1. **Sign in** as the administrator.
2. **Register a worker** (Workers → Register worker). Use a unique ID such as `W-1001`, plus name, department and contractor. Phone is optional.
3. **Enroll the face.** You are taken to the enrollment page automatically.
   - Read the on-screen **biometric notice** to the worker and tick the consent box.
   - Press **Start camera** and allow access in the browser.
   - Capture 3–8 samples: straight on, slightly left, slightly right, slightly up and down. Use even lighting and keep one person in frame.
   - Press **Save enrollment**. Rejected samples are outlined in red with the reason ("Too dark…", "More than one face…"). Remove them, retake, and save again.
   - If the samples resemble another worker, they are saved as *pending review* and the worker is **not** recognised until an admin resolves it under **Enrollment reviews**.
4. **Set up the kiosk.**
   - Same machine: click **Open kiosk** while signed in.
   - Dedicated device: Kiosk devices → create an activation code. On the device, open `/kiosk/activate` and enter the code.
5. **Check in:** the worker presses **Check in**, follows the instruction ("blink slowly two times" or "turn your head to YOUR left"), and sees *Checked in: name, time*. A second check-in the same day is rejected. **Check out** works the same way.
6. **Review and correct:** Attendance shows the filtered history. **Correct** edits times with a mandatory reason, and the record is marked *Admin entry / corrected*. Every change appears in **Audit**. **Export CSV** downloads the current filter.

## Screenshots

Captured from the running app (headless Chromium, fictional demo data, camera off):

| Dashboard | Face enrollment | Kiosk (phone width) |
|---|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Enrollment](docs/screenshots/enrollment.png) | ![Kiosk on a phone](docs/screenshots/kiosk-mobile.png) |

More: [login](docs/screenshots/login.png) · [workers](docs/screenshots/workers.png) · [kiosk](docs/screenshots/kiosk.png) · [attendance](docs/screenshots/attendance.png) · [audit](docs/screenshots/audit.png).

## Testing

```bash
pytest                                    # full suite on a temporary SQLite database
pytest tests/test_dlib_engine.py          # real dlib engine smoke test (synthetic, face-free images)
# MySQL-backed run, including concurrency tests (uses a disposable database; tables are dropped):
TEST_DATABASE_URL="mysql+pymysql://wfa_app:<password>@127.0.0.1:3306/worker_attendance_test?charset=utf8mb4" pytest
```

PowerShell: `$env:TEST_DATABASE_URL="mysql+pymysql://..."; pytest`.

The recognition tests use **mocked encodings** from a fake engine. They verify decision logic, not real-world accuracy. See [docs/testing.md](docs/testing.md), including how to measure false matches and false rejections with consented data.

## Known limitations

- **Spoofing:** the liveness check is a heuristic on 2D webcam frames. A video replay, mask, or deepfake stream can pass it. It is not production-grade presentation-attack detection (ISO/IEC 30107-3).
- **Accuracy is not measured here.** No real faces were used in development. Accuracy depends on your camera, lighting and population. Evaluate on site before relying on it (see [docs/testing.md](docs/testing.md)).
- **No overnight shifts:** check-out must fall on the same local date as check-in. Open records from earlier days need an admin correction.
- **No roster:** nobody is labelled absent.
- **Process-local caches:** the encoding cache re-validates against the DB on every lookup, so it stays correct across processes. The notification cooldown is per process.
- **Scale:** matching is brute-force NumPy over all active encodings. It is fine for thousands of workers but not tuned for very large deployments.
- **Latency:** the kiosk sends 16 frames per attempt. On the development server, HOG detection plus encoding and landmarks for 16 frames (640×480) took about 3.6 s of CPU (2.0 s detection + 1.5 s encoding/landmarks, measured on synthetic frames). Expect more on low-end hardware. Lower `LIVENESS_FRAME_COUNT` or `PROCESSING_MAX_WIDTH` to trade robustness for speed.

## Future improvements

Roster and shift policies (absence, overnight shifts, grace periods) · dedicated PAD/anti-spoofing models or depth/IR cameras · per-worker adaptive thresholds from evaluation data · offline kiosk queueing · i18n of kiosk prompts (Hindi and regional languages) · role-based admin permissions (supervisor vs HR) · encrypted-at-rest biometric columns with a KMS · production WSGI/TLS deployment recipe.

## Documentation

- [Architecture](docs/architecture.md)
- [Database schema](docs/database.md)
- [Recognition, thresholds and liveness](docs/recognition.md)
- [Testing and accuracy evaluation](docs/testing.md)
- [Security and privacy](docs/security-and-privacy.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Contributing](CONTRIBUTING.md)
