"""Database layer: visits, the weekly grid, day drill-down, and verification."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from birdwatcher.database import week_start_for


def test_week_start_for_is_sunday():
    # 2026-06-28 is a Sunday; the week start for any day that week is that Sunday.
    sunday = date(2026, 6, 28)
    assert week_start_for(sunday) == sunday
    assert week_start_for(date(2026, 7, 1)) == sunday      # Wed -> same Sun
    assert week_start_for(date(2026, 7, 4)) == sunday      # Sat -> same Sun
    assert week_start_for(date(2026, 7, 5)) == date(2026, 7, 5)  # next Sun


def test_add_visit_and_recent(db):
    vid = db.add_visit(species="Blue Jay", confidence=0.9,
                       first_ts=datetime(2026, 6, 28, 9, 0))
    assert isinstance(vid, int) and vid > 0
    recent = db.recent_visits(limit=5)
    assert len(recent) == 1
    assert recent[0]["species"] == "Blue Jay"


def test_week_grid_counts_visits_per_day(db):
    sunday = week_start_for(date(2026, 6, 28))
    base = datetime.combine(sunday, datetime.min.time())
    # 3 cardinal visits on day 0, 1 on day 2
    for h in (8, 9, 10):
        db.add_visit("Northern Cardinal", 0.8, first_ts=base + timedelta(hours=h))
    db.add_visit("Northern Cardinal", 0.8, first_ts=base + timedelta(days=2, hours=8))

    grid = db.week_grid(sunday)
    assert grid["start"] == sunday.isoformat()
    assert len(grid["days"]) == 7
    card = next(s for s in grid["species"] if s["name"] == "Northern Cardinal")
    assert card["counts"][0] == 3
    assert card["counts"][2] == 1
    assert card["total"] == 4


def test_verified_species_overrides_in_reads(db):
    vid = db.add_visit("Unknown bird", 0.3, image_path="x.jpg",
                       first_ts=datetime(2026, 6, 28, 9, 0))
    db.set_verified(vid, "House Finch")
    # recent_visits COALESCEs verified over raw species
    assert db.recent_visits(1)[0]["species"] == "House Finch"
    ver, total = db.review_counts()
    assert (ver, total) == (1, 1)


def test_day_hours_buckets_by_hour(db):
    day = date(2026, 6, 28)
    base = datetime.combine(day, datetime.min.time())
    db.add_visit("Mourning Dove", 0.7, first_ts=base.replace(hour=7))
    db.add_visit("Mourning Dove", 0.7, first_ts=base.replace(hour=7, minute=30))
    db.add_visit("Mourning Dove", 0.7, first_ts=base.replace(hour=15))
    out = db.day_hours(day)
    dove = next(s for s in out["species"] if s["name"] == "Mourning Dove")
    assert dove["counts"][7] == 2
    assert dove["counts"][15] == 1
    assert len(dove["counts"]) == 24
