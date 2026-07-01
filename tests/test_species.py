from __future__ import annotations

from datetime import datetime

from birdwatcher.database import Database


def test_species_detail_buckets_by_hour(tmp_path):
    db = Database(tmp_path / "bw.db")
    db.add_visit("Carolina Chickadee", 0.9, image_path="a.jpg", first_ts=datetime(2026, 7, 1, 8, 30))
    db.add_visit("Carolina Chickadee", 1.0, image_path="b.jpg", first_ts=datetime(2026, 7, 1, 17, 15))

    d = db.species_detail("Carolina Chickadee")
    assert d["total"] == 2
    assert d["hourly"][8] == 1 and d["hourly"][17] == 1
    assert len(d["photos"]) == 2
    assert d["best_confidence"] == 1.0
    assert d["first_ts"].startswith("2026-07-01T08")   # oldest
    assert d["last_ts"].startswith("2026-07-01T17")    # newest
    db.close()


def test_species_api_route(cfg, client):
    db = Database(cfg.paths.db_path())
    db.add_visit("Northern Cardinal", 0.95, image_path="c.jpg", first_ts=datetime.now())
    db.close()

    j = client.get("/api/species/Northern%20Cardinal").get_json()
    assert j["name"] == "Northern Cardinal"
    assert j["kind"] == "bird"
    assert j["total"] >= 1
    # the HTML page renders too
    assert client.get("/species/Northern%20Cardinal").status_code == 200
