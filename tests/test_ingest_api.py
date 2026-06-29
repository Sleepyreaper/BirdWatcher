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


def test_ingest_rejects_negative_frames(client):
    r = client.post("/api/ingest", json=_payload(frames=-1))
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_frames"


def test_ingest_rejects_bad_base64(client):
    r = client.post("/api/ingest", json=_payload(image_b64="%%%notb64%%%"))
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_image"


def test_ingest_rejects_path_traversal_on_captures(client):
    r = client.get("/captures/../../etc/passwd")
    assert r.status_code == 404


def test_ingest_success_writes_file_and_row(client):
    r = client.post("/api/ingest", json=_payload())
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
