# Security and privacy

Face images and face encodings are **sensitive biometric data**. In India they are personal data under the Digital Personal Data Protection Act, 2023, and similar rules apply elsewhere (e.g. GDPR Art. 9). This document describes what the software does. Your organisation is still responsible for lawful purpose, notice, consent, and responding to requests to access or delete data.

## Data inventory

| Data | Where | Retained | Notes |
|---|---|---|---|
| Face encodings (128 floats) | `face_encodings.encoding` | Until deleted by an admin, or by the retention purge after deactivation | Can identify a person when compared with their face; treat as biometric |
| Raw enrollment photos | `instance/biometric/` (or `BIOMETRIC_STORAGE_DIR`) | **Not stored by default.** Only if `STORE_ENROLLMENT_IMAGES=true` | Files mode 0600, random names, directory mode 0700, outside `static/`, never served over HTTP |
| Kiosk frames | Memory only, during one request | Discarded after processing | Never written to disk or logs |
| Worker metadata | `workers` | Kept for attendance history | Phone number optional |
| Attendance | `attendance_records` | Per your HR policy | Includes match distance (not an image) |
| Audit trail | `audit_events` | Per your policy | No images, encodings or secrets; includes IP address |
| Consent | `workers.consent_given_at`, `consent_notice_version`, `consent_recorded_by_id` | With the worker record | Captured at first enrollment |

## Notice and consent

The enrollment page shows a notice that the admin must read to the worker in a language they understand. The worker's acknowledgment (a checkbox) is required before the first enrollment is accepted. The server enforces this, not just the page. The notice states the purpose, that photos are not kept (unless configured), the retention policy, that recognition can make mistakes and a manual entry is always available, and that refusing must not affect wages or work. Change the notice text in `app/templates/workers/enroll.html` and bump `CONSENT_NOTICE_VERSION`.

Offer a non-biometric alternative to workers who decline. Admins can create manual attendance records (marked **Admin entry**).

## Deletion and retention

- **One worker, on request:** worker profile → *Delete this worker's face data* (reason and confirmation required, audited). CLI: `flask --app wsgi biometrics purge-worker W-1001 --reason "Worker request"`.
- **Retention after deactivation:** `flask --app wsgi biometrics purge-inactive` deletes face data of workers deactivated more than `BIOMETRIC_RETENTION_DAYS` (default 30) days ago. Use `--dry-run` to preview. Schedule it daily with cron (`0 2 * * * cd /srv/wfa && .venv/bin/flask --app wsgi biometrics purge-inactive`) or Windows Task Scheduler.
- **Rejected reviews** delete the pending samples. Deleting face data also deletes any retained photo files.
- Attendance records are **not** deleted with face data. They are employment records under your HR retention policy.
- **Database backups** still contain deleted encodings until those backups expire. Set backup retention to match.

## Application security controls

| Control | Implementation |
|---|---|
| Passwords | Werkzeug `generate_password_hash` (scrypt); never stored or logged in plaintext; minimum 12 characters for CLI-created admins |
| Brute force | Lockout after `LOGIN_MAX_ATTEMPTS` (5) failures for `LOGIN_LOCKOUT_MINUTES` (15); generic error message |
| Sessions | Signed cookies, `HttpOnly`, `SameSite=Lax`, `Secure` in production; Flask-Login strong session protection; session cleared at login |
| CSRF | Flask-WTF `CSRFProtect` on every POST, including JSON (`X-CSRFToken` header) |
| Authorisation | All worker, enrollment, review, attendance, report, export, correction, audit and device pages require an admin; kiosk APIs require an admin session or an activated, unrevoked kiosk device |
| Kiosk devices | 192-bit one-time activation code shown once; only SHA-256 stored; revocation ends the device's access on its next request |
| Liveness tokens | 256-bit random; SHA-256 stored; single use; 30 s TTL; tied to session and mode |
| SQL injection | SQLAlchemy ORM / parameterised statements only; LIKE searches use `autoescape` |
| Unsafe deserialisation | Encodings stored as raw float64 bytes; never pickle |
| Upload validation | JPEG/PNG magic bytes + Pillow format check, byte and dimension limits, decompression-bomb guard, `MAX_CONTENT_LENGTH` |
| Output encoding | Jinja2 autoescaping; JS builds DOM with `textContent` |
| CSV injection | Cells starting with `= + - @ \t \r \n \| %` are prefixed with `'` |
| Headers | CSP `default-src 'self'` with no inline scripts, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy: camera=(self)`, `Cache-Control: no-store` on dynamic pages |
| Logging | Request bodies (images) are never logged; encodings and secrets are never logged; health endpoint reveals no versions or errors |
| Secrets | From environment / `.env` (git-ignored); placeholder secrets refused at startup |
| Network | Dev server binds `127.0.0.1`; MySQL container publishes only on `127.0.0.1` |

## What must never be committed

`.env`, credentials and keys, `instance/` (biometric storage), face images, `*.npy`, CSV exports, database dumps and SQLite files. All are listed in `.gitignore`. Before each commit, check `git status` and `git diff --cached --stat` for stray files.

## Non-local deployment

Running beyond `localhost` needs more than this repository provides:

- **HTTPS is mandatory.** Browsers allow camera access only in secure contexts, and session cookies and biometric frames must be encrypted in transit. Put a reverse proxy (nginx or Caddy) with a valid TLS certificate in front, and set `APP_ENV=production` (secure cookies).
- Use a production WSGI server (e.g. `gunicorn -w 2 -b 127.0.0.1:8000 wsgi:app` behind the proxy). Do not expose the Flask development server.
- Add `ProxyFix` if behind a proxy so IP addresses in the audit trail are correct, and configure the proxy's request size limit to at least `MAX_CONTENT_LENGTH`.
- Use separate MySQL credentials with minimal privileges, encrypted backups, OS-level disk encryption, and patching.
- Add network-level rate limiting for `/login` and `/kiosk/api/*`.
- Restrict admin access (VPN or IP allow-list) and consider multi-factor authentication.
- Set up monitoring of `/healthz` and log retention.
- Do a privacy impact assessment for biometric processing before go-live.

## Reporting a vulnerability

Do not open a public issue. Contact the repository owner privately with details and reproduction steps.
