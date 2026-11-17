# Contributing

## Setup

Follow the README installation, then `pip install -r requirements-dev.txt`.

## Ground rules

- **Never commit biometric data**: face images, encodings, `instance/`, database dumps, CSV exports, `.env` or credentials. Run `git status` and `git diff --cached --stat` before every commit.
- **Never use real people's faces in tests or fixtures.** Use the fake engine in `tests/fakes.py`. Real-face evaluation follows the consented protocol in [docs/testing.md](docs/testing.md) and stays out of the repository.
- **No accuracy claims** without a documented, consented evaluation, including confidence intervals.
- Do not describe the liveness check as anti-spoofing.
- Never deserialise untrusted data with `pickle` or similar.
- Don't log request bodies, images, encodings or secrets.

## Code layout

- Business rules go in `app/services/`. Blueprints stay thin: parse input, call a service, render or return JSON.
- Anything touching attendance uniqueness must rely on database constraints or conditional updates, not only on checks in Python.
- Changes to biometric or attendance data must write an `audit.record(...)` event in the same transaction.
- Invalidate the encoding cache (`recognition.invalidate_cache()`) after changing encodings or worker status.
- Keep templates free of inline `<script>` and `style` attributes, because the CSP forbids them.

## Database changes

1. Edit `app/models.py`.
2. `flask --app wsgi db migrate -m "short description"`.
3. **Review** the generated migration (types, constraint names, MySQL specifics) and add data migrations if needed.
4. `flask --app wsgi db upgrade`, then `pytest tests/test_migrations.py` (it fails if models and migrations diverge).
5. Update [docs/database.md](docs/database.md).

## Tests

- Add or update tests for every behaviour change. Recognition logic tests use mocked encodings. Say so in the test docstring if it isn't obvious.
- Run `pytest` (SQLite) and, for anything involving transactions or concurrency, run it with `TEST_DATABASE_URL` pointing at the MySQL test database.

## Commits and pull requests

- Small, focused commits with imperative messages ("Add retention purge command").
- Pull requests describe the change, how it was tested (SQLite and/or MySQL), and any privacy or security impact.
- Branch from `main`; do not force-push shared branches.
