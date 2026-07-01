from __future__ import annotations

from datetime import datetime

from birdwatcher.database import Database, week_start_for


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
