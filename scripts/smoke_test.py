#!/usr/bin/env python3
"""HTTP smoke test against a running server (stdlib only).

    SMOKE_PASSWORD=... python scripts/smoke_test.py --base-url http://127.0.0.1:5000 --username admin

Checks: health endpoint, login page, CSRF-protected login, dashboard, worker registration,
worker pages, enrollment page, kiosk page + challenge API, attendance history and CSV export,
audit page, logout. Creates one worker named SMOKE-<timestamp> (fictional metadata only).
Does not exercise the camera or face recognition.
"""
from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class Session:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())
        self.csrf = None

    def request(self, method, path, data=None, json_body=None, headers=None):
        headers = dict(headers or {})
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
            headers["X-CSRFToken"] = self.csrf or ""
        elif data is not None:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            resp = self.opener.open(req, timeout=30)
        except urllib.error.HTTPError as err:
            resp = err
        text = resp.read().decode("utf-8", "replace")
        m = re.search(r'name="csrf-token" content="([^"]+)"', text)
        if m:
            self.csrf = m.group(1)
        return resp.status, resp.headers, text


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:5000")
    ap.add_argument("--username", default="admin")
    args = ap.parse_args()
    password = os.environ.get("SMOKE_PASSWORD") or getpass.getpass("Admin password: ")
    s = Session(args.base_url)
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}{(' - ' + detail) if detail and not cond else ''}")

    st, _h, body = s.request("GET", "/healthz")
    check("GET /healthz -> 200 ok", st == 200 and json.loads(body) == {"status": "ok", "database": "ok"}, body)
    st, h, body = s.request("GET", "/login")
    check("GET /login -> 200", st == 200 and "Administrator sign in" in body)
    check("CSP header present", "default-src 'self'" in h.get("Content-Security-Policy", ""))
    st, _h, _b = s.request("POST", "/login", data={"username": args.username, "password": password})
    check("POST /login without CSRF -> 400", st == 400)
    st, h, _b = s.request("POST", "/login", data={"username": args.username, "password": password, "csrf_token": s.csrf})
    check("POST /login with CSRF -> 302", st == 302, f"status {st}")
    st, _h, body = s.request("GET", "/")
    check("GET / dashboard -> 200", st == 200 and "Dashboard" in body)

    code = f"SMOKE-{int(time.time()) % 100000}"
    s.request("GET", "/workers/new")
    st, h, _b = s.request("POST", "/workers/new", data={"worker_code": code, "full_name": "Smoke Test Worker",
                                                        "department": "QA", "contractor": "Smoke Contractor (fictional)",
                                                        "phone": "", "csrf_token": s.csrf})
    location = h.get("Location", "")
    check("register worker -> 302 to enrollment", st == 302 and "/enroll" in location, f"{st} {location}")
    st, _h, body = s.request("GET", f"/workers/?q={code}")
    check("worker list search finds worker", st == 200 and code in body)
    worker_path = location.replace("/enroll", "") if location else ""
    if worker_path:
        st, _h, body = s.request("GET", urllib.parse.urlsplit(worker_path).path)
        check("worker profile -> 200", st == 200 and code in body)
        st, _h, body = s.request("GET", urllib.parse.urlsplit(location).path)
        check("enrollment page -> 200 with consent notice", st == 200 and "Biometric enrollment notice" in body)
    st, _h, body = s.request("GET", "/kiosk/")
    check("kiosk page -> 200", st == 200 and "Attendance kiosk" in body)
    st, _h, body = s.request("POST", "/kiosk/api/challenge", json_body={"mode": "check_in"})
    data = json.loads(body) if body.startswith("{") else {}
    check("kiosk challenge issued", st == 200 and data.get("ok") and data.get("challenge_type") in
          {"blink", "turn_left", "turn_right", "none"}, body[:200])
    st, _h, body = s.request("POST", "/kiosk/api/verify", json_body={"token": data.get("token"), "mode": "check_in",
                                                                     "frames": []})
    check("kiosk verify rejects empty frames", st == 200 and json.loads(body)["status"] == "invalid_request", body[:200])
    st, _h, body = s.request("POST", "/kiosk/api/verify", json_body={"token": data.get("token"), "mode": "check_in",
                                                                     "frames": []})
    check("kiosk challenge replay rejected", json.loads(body)["status"] == "challenge_replayed", body[:200])
    st, _h, body = s.request("GET", "/attendance/")
    check("attendance history -> 200", st == 200 and "Attendance history" in body)
    st, h, body = s.request("GET", "/attendance/export.csv")
    check("CSV export -> 200 text/csv", st == 200 and h.get("Content-Type", "").startswith("text/csv")
          and body.startswith("business_date,"))
    st, _h, body = s.request("GET", "/audit/")
    check("audit page -> 200 shows worker.create", st == 200 and "worker.create" in body)
    s.request("GET", "/")
    st, _h, _b = s.request("POST", "/logout", data={"csrf_token": s.csrf})
    check("logout -> 302", st == 302)
    st, h, _b = s.request("GET", "/")
    check("dashboard after logout redirects to login", st == 302 and "/login" in h.get("Location", ""))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
