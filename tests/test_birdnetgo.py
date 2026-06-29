from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from birdwatcher.birdnetgo import BirdnetGoReader


def _make_db(path: Path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE labels (id INTEGER PRIMARY KEY, scientific_name TEXT)")
    con.execute(
        "CREATE TABLE detections (detected_at INTEGER, confidence REAL, label_id INTEGER, unlikely INTEGER)"
    )
    con.execute("INSERT INTO labels (id, scientific_name) VALUES (1, 'Cardinalis cardinalis')")
    con.commit()
    return con


def test_birdnetgo_heard_week_skips_bad_rows(tmp_path: Path):
    db = tmp_path / "birdnet.db"
    con = _make_db(db)
    con.execute("INSERT INTO detections VALUES (?, ?, ?, 0)", (int(datetime(2026, 6, 29, 8, 0).timestamp()), 0.9, 1))
    con.execute("INSERT INTO detections VALUES (?, ?, ?, 0)", ("bad-epoch", 0.7, 1))
    con.commit()
    con.close()

    r = BirdnetGoReader(db)
    out = r.heard_week(date(2026, 6, 28), {"Cardinalis cardinalis": "Northern Cardinal"})
    assert out["Northern Cardinal"][1] == 1


def test_birdnetgo_recent_returns_empty_on_query_failure(tmp_path: Path):
    db = tmp_path / "birdnet.db"
    db.write_text("not sqlite", encoding="utf-8")
    r = BirdnetGoReader(db)
    assert r.recent({}) == []
