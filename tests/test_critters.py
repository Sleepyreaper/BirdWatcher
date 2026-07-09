from __future__ import annotations

from datetime import datetime

from birdwatcher.database import Database


def test_source_filter_scopes_the_week(cfg, client):
    """/api/week?source= scopes to one camera; the CritterWatch page renders."""
    db = Database(cfg.paths.db_path())
    now = datetime.now()
    db.add_visit("Northern Cardinal", 0.9, image_path="c.jpg", source="feeder", first_ts=now)
    db.add_visit("White-tailed Deer", 0.8, image_path="d.jpg", source="creek", first_ts=now)
    db.close()

    feeder = client.get("/api/week?source=feeder").get_json()
    creek = client.get("/api/week?source=creek").get_json()
    assert "Northern Cardinal" in [s["name"] for s in feeder["seen"]]
    assert "White-tailed Deer" in [c["name"] for c in creek["critters"]]
    assert "White-tailed Deer" not in [c["name"] for c in feeder["critters"]]
    assert client.get("/critters").status_code == 200


def test_critters_split_from_birds(cfg, client):
    """A critter sighting files under 'critters', a bird under 'seen'."""
    db = Database(cfg.paths.db_path())
    now = datetime.now()
    db.add_visit("Northern Cardinal", 0.9, image_path="bird.jpg", first_ts=now, last_ts=now)
    db.add_visit("Eastern Chipmunk", 0.8, image_path="chip.jpg", first_ts=now, last_ts=now)
    db.close()

    j = client.get("/api/week").get_json()
    seen = {s["name"] for s in j["seen"]}
    critters = {c["name"] for c in j["critters"]}

    assert "Northern Cardinal" in seen
    assert "Eastern Chipmunk" in critters
    assert "Eastern Chipmunk" not in seen          # not counted as a bird
    assert j["stats"]["species_seen"] == 1          # bird count excludes the critter
    assert j["stats"]["critters"] >= 1
