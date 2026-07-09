from __future__ import annotations

from datetime import datetime

from birdwatcher.database import Database, week_start_for


def test_add_visit_stores_source(tmp_path):
    """Each sighting is tagged with its camera; defaults to 'feeder'."""
    db = Database(tmp_path / "bw.db")
    db.add_visit("Raccoon", 0.8, image_path="r.jpg", source="creek")
    db.add_visit("Northern Cardinal", 0.9, image_path="c.jpg")
    rows = db._conn.execute("SELECT species, source FROM sightings ORDER BY id").fetchall()
    assert rows[0]["source"] == "creek"
    assert rows[1]["source"] == "feeder"
    db.close()


def test_week_grid_best_and_latest_differ(tmp_path):
    """thumb = highest-confidence crop; latest = most recent crop."""
    db = Database(tmp_path / "bw.db")
    ws = week_start_for(datetime(2026, 6, 29).date())
    d1 = datetime.combine(ws, datetime.min.time()).replace(hour=8)
    d2 = d1.replace(hour=15)
    db.add_visit("House Finch", 0.95, image_path="best.jpg", first_ts=d1, last_ts=d1)
    db.add_visit("House Finch", 0.40, image_path="recent.jpg", first_ts=d2, last_ts=d2)

    sp = next(s for s in db.week_grid(ws)["species"] if s["name"] == "House Finch")
    assert sp["thumb"] == "best.jpg"      # sharpest / most confident
    assert sp["latest"] == "recent.jpg"   # most recent visit
    db.close()
