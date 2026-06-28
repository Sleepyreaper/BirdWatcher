"""Web API: /api/ingest auth + validation hardening, and date-arg validation.

These cover the security-sensitive surface the team flagged: the shared-secret
gate, base64 safety, server-generated filenames, size caps, and clean 400s on
malformed input (instead of 500s)."""
from __future__ import annotations

import base64


def _ingest_body(token="s3cret", **over):
    body = {
        "token": token,
        "first_ts": "2026-06-28T09:00:00",
        "species": "Blue Jay",
        "confidence": 0.9,
        "frames": 3,
    }
    body.update(over)
    return body


def test_ingest_happy_path(client):
    r = client.post("/api/ingest", json=_ingest_body())
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_ingest_rejects_wrong_token(client):
    r = client.post("/api/ingest", json=_ingest_body(token="nope"))
    assert r.status_code == 403


def test_ingest_rejects_missing_token(client):
    body = _ingest_body()
    del body["token"]
    r = client.post("/api/ingest", json=body)
    assert r.status_code == 403


def test_ingest_bad_first_ts_is_400_not_500(client):
    r = client.post("/api/ingest", json=_ingest_body(first_ts="not-a-date"))
    assert r.status_code == 400


def test_ingest_bad_base64_is_400(client):
    r = client.post("/api/ingest", json=_ingest_body(image_b64="!!!not-base64!!!"))
    assert r.status_code == 400


def test_ingest_oversized_image_is_413(client, cfg):
    # cfg caps max_image_bytes at 1 MB; send ~1.4 MB decoded.
    big = base64.b64encode(b"\x00" * 1_400_000).decode()
    r = client.post("/api/ingest", json=_ingest_body(image_b64=big))
    assert r.status_code == 413


def test_ingest_writes_path_safe_filename(client, cfg):
    # A hostile species value must not escape the captures dir or inject separators.
    payload = _ingest_body(species="../../etc/passwd",
                           image_b64=base64.b64encode(b"\xff\xd8\xff\x00").decode())
    r = client.post("/api/ingest", json=payload)
    assert r.status_code == 200
    captures = cfg.paths.captures_path()
    written = list(captures.rglob("*.jpg"))
    assert written, "expected a capture file"
    for p in written:
        assert captures in p.resolve().parents  # never escaped the captures root
        assert ".." not in p.name


def test_week_bad_date_is_400(client):
    assert client.get("/api/week?start=garbage").status_code == 400
    assert client.get("/api/day?date=2026-13-99").status_code == 400


def test_week_ok_without_params(client):
    r = client.get("/api/week")
    assert r.status_code == 200
    assert "seen" in r.get_json()
