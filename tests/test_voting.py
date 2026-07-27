from __future__ import annotations

import pytest

from birdwatcher.voting import tally_votes


def test_unanimous_votes_agree_fully():
    out = tally_votes([("Raccoon", 0.8)] * 5)
    assert out.species == "Raccoon"
    assert out.votes == 5 and out.total == 5
    assert out.agreement == pytest.approx(1.0)


def test_majority_wins_over_a_lone_wrong_frame():
    votes = [("American Beaver", 0.95)] + [("Raccoon", 0.80)] * 4
    out = tally_votes(votes)
    assert out.species == "Raccoon"           # 4 beats 1, despite the beaver's higher conf
    assert out.agreement == pytest.approx(0.8)


def test_ties_break_toward_more_summed_confidence():
    # 2 vs 2: raccoon has more total evidence, so it wins the tie.
    votes = [("American Beaver", 0.60), ("American Beaver", 0.55),
             ("Raccoon", 0.90), ("Raccoon", 0.85)]
    out = tally_votes(votes)
    assert out.species == "Raccoon"
    assert out.votes == 2 and out.total == 4
    assert out.agreement == pytest.approx(0.5)


def test_accepts_objects_with_species_and_confidence_attrs():
    from types import SimpleNamespace
    votes = [SimpleNamespace(species="Blue Jay", confidence=0.7),
             SimpleNamespace(species="Blue Jay", confidence=0.6),
             SimpleNamespace(species="Crow", confidence=0.9)]
    out = tally_votes(votes)
    assert out.species == "Blue Jay" and out.votes == 2


def test_empty_input_returns_none():
    assert tally_votes([]) is None
