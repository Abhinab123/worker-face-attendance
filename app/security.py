from __future__ import annotations

from urllib.parse import urlsplit


def safe_next_url(target: str | None) -> str | None:
    """Only allow same-site relative redirects after login (no open redirect)."""
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    return target


def is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}
