from __future__ import annotations

from datetime import datetime

from birdwatcher.database import Database


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
