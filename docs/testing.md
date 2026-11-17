# Testing

## Running tests

```bash
source .venv/bin/activate              # Windows: .\.venv\Scripts\Activate.ps1
pytest                                 # whole suite, temporary SQLite DB per test
pytest -k kiosk -v                     # a subset
TEST_DATABASE_URL="mysql+pymysql://wfa_app:<pw>@127.0.0.1:3306/worker_attendance_test?charset=utf8mb4" pytest
```

`TEST_DATABASE_URL` must point at a **disposable** database: every test drops and recreates all tables. With MySQL configured, the whole suite runs on MySQL **and** the concurrency tests in `test_mysql_concurrency.py` run. Without it they are skipped, and pytest reports the skips.

## What is covered

| File | Covers |
|---|---|
| `test_auth.py` | Login/logout, generic error messages, lockout, CSRF required, open-redirect protection, admin-only routes, disabled admins, `create-admin` CLI, kiosk-device sessions blocked from admin pages |
| `test_workers.py` | Registration, ID normalisation, duplicate IDs (service and DB constraint), validation, edit with audit, deactivation keeps history, search/filters with LIKE-escaping, fictional demo seeding |
| `test_enrollment.py` | Consent required, sample-count limits, per-sample rejection reasons (multiple faces, no face, dark, small face), invalid formats, dimension and size limits, mixed-person sample sets, inactive workers, duplicate-identity flagging (also against inactive workers), review approve/reject, face-data deletion, retention purge CLI, photo storage only when configured (mode 0600, outside `static/`), codec safety |
| `test_recognition.py` | Threshold/ambiguity decision rules, known/unknown/ambiguous outcomes, inactive-worker exclusion, cache invalidation (explicit and out-of-band), pending encodings unused, multi-frame identity consistency |
| `test_kiosk.py` | Full HTTP flow for each challenge type, wrong-direction turn, static photo fails blink, unknown/multiple faces, poor lighting, ambiguous match, inactive worker, duplicate check-in, cooldown, check-out before check-in, challenge **replay**, **expiry**, session/mode binding, hashed tokens, unauthorised kiosk, device activation and revocation, pruning |
| `test_liveness.py` | EAR and yaw maths; blink and turn detection, including photo-like static sequences |
| `test_attendance.py` | Check-in/out, duplicate check-in/out, check-out before check-in, inactive workers, DB unique and CHECK constraints, **IST midnight boundaries**, configurable timezone, overnight rejection, UTC storage, corrections (reason required, audit before/after/actor, method relabelled to admin, overnight/future rejected), manual records |
| `test_reports.py` | Filters (date, worker, department, contractor), CSV columns and local times, **formula injection** neutralised, export audited, invalid dates handled, no "absent" labels, dashboard counts |
| `test_security.py` | Health endpoint content, CSP and other headers, cookie flags, no inline scripts, `next` URL safety, image validation, config validation, image payloads absent from logs |
| `test_migrations.py` | Migrations build exactly the model schema (Alembic `compare_metadata`), then downgrade cleanly |
| `test_mysql_concurrency.py` | 12 threads racing on check-in, check-out and challenge consumption against MySQL: exactly one success each |
| `test_dlib_engine.py` | The **real** dlib engine loads, finds no face in noise, produces 128-d encodings and 68-point landmarks |

## Mocked recognition vs real biometric accuracy

Almost all recognition tests use `tests/fakes.py::FakeEngine`. Each synthetic PNG carries a colour tag, and the fake engine returns pre-registered **mock** encodings and landmarks for that tag. Synthetic "people" are random 128-d vectors scaled so that different people are about 0.9 apart and samples of one person about 0.2 apart, which mimics typical dlib distances.

This tests the **logic**: thresholds, ambiguity, consistency, liveness rules, transactions. It says **nothing** about how well dlib recognises real faces at your site. No real faces were used to develop or test this project, and no accuracy figures are claimed.

`test_dlib_engine.py` only checks that the real library is installed and wired correctly, using images that contain no faces.

## Evaluating false matches and false rejections with consented data

Do this before go-live, and again after changing cameras, lighting or thresholds.

1. **Consent and scope.** Recruit volunteers who give written consent for an *evaluation*, separate from attendance enrollment. Record the purpose, retention period (e.g. deleted 30 days after the evaluation) and who can access the data. Aim for at least 50–100 people who reflect the workforce (age, gender, skin tone, facial hair, typical headgear).
2. **Enroll** each volunteer through the normal UI on the actual kiosk hardware and in the actual lighting.
3. **Collect probe attempts** on different days and at different times (morning sun, evening, artificial light): at least 5 *genuine* attempts per person. Do not store probe images unless the consent covers it. Instead record, per attempt, the volunteer's true ID and the system's result from the audit trail and `attendance_records.check_in_distance`, or a small evaluation script calling `match_encoding`.
4. **Impostor attempts.** Each volunteer also presents at the kiosk "as" other people. With 1:N identification, every attempt by a person is effectively compared against everyone else's templates, so record any attempt where the system returns a *different* enrolled worker.
5. **Compute:**
   - **False rejection rate (FRR):** genuine attempts returning unknown or ambiguous, divided by genuine attempts.
   - **False match rate (FMR, or false positive identification rate):** attempts identified as the *wrong* worker, divided by all attempts. This is the critical metric for payroll.
   - Report both **with 95% confidence intervals** (e.g. Wilson intervals). With small samples, zero observed false matches does **not** mean zero risk: by the "rule of three", 0 errors in *n* trials means the true rate is below 3/*n* at 95% confidence.
6. **Sweep thresholds offline.** Log the best and runner-up distances of each attempt and recompute FMR/FRR for thresholds 0.35–0.60 to draw a DET curve. Pick the threshold that keeps FMR acceptably low, then set `FACE_MATCH_THRESHOLD`.
7. **Check subgroups.** Compute FRR per subgroup to detect uneven performance. If one group is rejected far more often, fix lighting or enrollment first. Do not lower the threshold for everyone.
8. **Presentation attacks.** Separately try printed photos, phone photos and phone videos of volunteers against each challenge type, and record the attack success rate. Expect video replays to succeed sometimes (see [recognition.md](recognition.md#limits-of-liveness-and-proxy-protection)).
9. **Delete** evaluation data at the end, as promised in the consent.

## Manual verification checklist (needs a person and a camera)

- [ ] `/workers/<id>/enroll` → Start camera → the browser asks for permission; denying shows the permission guidance; allowing shows the mirrored preview.
- [ ] Capture 3+ samples in good light → "Enrollment saved: N face samples are active."
- [ ] Capture with two people in frame → that sample is rejected with "More than one face…".
- [ ] Capture in a dark room → "Too dark…".
- [ ] Kiosk → Check in → follow the blink instruction → "Checked in: <name>".
- [ ] Repeat Check in → "Already recorded" (cooldown) or "Already checked in today at HH:MM".
- [ ] Turn-left and turn-right challenges succeed when performed correctly and fail when performed the wrong way. Reload until each type appears.
- [ ] Holding a printed photo → rejected (no blink or turn).
- [ ] An unenrolled person → "Face not recognised".
- [ ] Check out → "Checked out: <name>".
- [ ] Attendance page shows both times in local time; Export CSV opens correctly in a spreadsheet.
