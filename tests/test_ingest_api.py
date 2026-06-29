from __future__ import annotations

import base64
from datetime import datetime


def _payload(**overrides):
    data = {
        "token": "secret-token",
        "species": "Northern Cardinal",
        "confidence": 0.91,
        "detector_conf": 0.88,
        "first_ts": datetime(2026, 6, 29, 8, 30, 0).isoformat(),
        "last_ts": datetime(2026, 6, 29, 8, 31, 0).isoformat(),
        "frames": 3,
        "image_b64": base64.b64encode(b"fakejpgbytes").decode("ascii"),
    }
    data.update(overrides)
    return data


def test_ingest_requires_json_content_type(client):
    r = client.post("/api/ingest", data="{}", content_type="text/plain")
    assert r.status_code == 415
    assert r.get_json()["error"] == "content_type_required"


def test_ingest_rejects_invalid_json(client):
    r = client.post("/api/ingest", data="{", content_type="application/json")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_json"


def test_ingest_rejects_missing_required_field(client):
    p = _payload()
    p.pop("species")
    r = client.post("/api/ingest", json=p)
    assert r.status_code == 400
    assert r.get_json()["error"] == "missing_fields"


def test_ingest_rejects_wrong_frames_type(client):
    r = client.post("/api/ingest", json=_payload(frames="abc"))
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_frames"


def test_ingest_rejects_negative_frames(client):
    r = client.post("/api/ingest", json=_payload(frames=-1))
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_frames"


def test_ingest_rejects_unknown_keys(client):
    r = client.post("/api/ingest", json=_payload(extra="nope"))
    assert r.status_code == 400
    assert r.get_json()["error"] == "unexpected_fields"


def test_ingest_rejects_bad_base64(client):
    r = client.post("/api/ingest", json=_payload(image_b64="%%%notb64%%%"))
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_image"


def test_ingest_rejects_oversized_image(client):
    raw = b"a" * (2 * 1024 * 1024 + 1)
    r = client.post("/api/ingest", json=_payload(image_b64=base64.b64encode(raw).decode("ascii")))
    assert r.status_code == 413
    assert r.get_json()["error"] == "image_too_large"


def test_ingest_rejects_path_traversal_on_captures(client):
    r = client.get("/captures/../../etc/passwd")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"


def test_ingest_rejects_directory_target(client, cfg):
    captures_dir = cfg.paths.captures_path()
    captures_dir.mkdir(parents=True, exist_ok=True)
    day_dir = captures_dir / "2026-06-29"
    day_dir.mkdir(parents=True, exist_ok=True)
    r = client.get("/captures/2026-06-29")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"


def test_ingest_success_writes_file_and_row(client):
    r = client.post("/api/ingest", json=_payload())
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
