"""Verify IntesaPay-Signature: t=<unix>,v1=<hmac-sha256-hex of '{t}.{body}'>."""

from __future__ import annotations

import hashlib
import hmac
import time


def verify_signature(
    secret: str,
    header: str | None,
    body: bytes | str,
    *,
    tolerance_seconds: int = 300,
) -> tuple[bool, str]:
    if not secret:
        return False, "INTESAPAY_WEBHOOK_SECRET not configured"
    if not header:
        return False, "missing IntesaPay-Signature header"

    parts: dict[str, str] = {}
    for chunk in header.split(","):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        parts[k.strip()] = v.strip()

    ts = parts.get("t")
    v1 = parts.get("v1")
    if not ts or not v1:
        return False, "signature header missing t= or v1="

    try:
        ts_i = int(ts)
    except ValueError:
        return False, "invalid timestamp"

    if abs(time.time() - ts_i) > tolerance_seconds:
        return False, f"timestamp outside tolerance ({tolerance_seconds}s)"

    raw = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body
    payload = f"{ts}.{raw}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, v1):
        return False, "v1 mismatch"
    return True, "ok"
