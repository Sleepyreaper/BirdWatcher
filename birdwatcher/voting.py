"""Temporal voting: decide a visit's species from *many* frames, not one.

The old pipeline kept the single sharpest crop of a visit and classified it
once — so one lucky-but-wrong frame (a woodpecker caught mid-turn that reads as
a beaver) decided the whole visit. Voting instead classifies several frames and
lets them vote: the species most frames agree on wins, and how strongly they
agreed becomes an honest confidence signal the naturalist can trust.

This module is deliberately pure (no OpenCV/torch) so the tally logic is unit
testable without a GPU or a model. The pipeline feeds it the per-frame
classifier results and reads back the consensus.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


@dataclass
class VoteOutcome:
    species: str          # the species the most frames agreed on
    votes: int            # how many frames voted for the winner
    total: int            # how many frames voted at all

    @property
    def agreement(self) -> float:
        """Fraction of frames that backed the winner (1.0 = unanimous)."""
        return self.votes / self.total if self.total else 0.0


def _species_of(item) -> str:
    return item.species if hasattr(item, "species") else item[0]


def _confidence_of(item) -> float:
    return float(item.confidence if hasattr(item, "confidence") else item[1])


def tally_votes(results: Iterable) -> VoteOutcome | None:
    """Tally per-frame classifications into a consensus.

    `results` is any iterable of things with `.species`/`.confidence` (a
    SpeciesResult) or `(species, confidence)` tuples. The winner is the species
    with the most votes; ties break toward the species with the most summed
    confidence (more total evidence). Returns None for an empty input.
    """
    by_species: dict[str, list[float]] = defaultdict(list)
    total = 0
    for r in results:
        by_species[_species_of(r)].append(_confidence_of(r))
        total += 1
    if not by_species:
        return None
    winner = max(by_species, key=lambda s: (len(by_species[s]), sum(by_species[s])))
    return VoteOutcome(species=winner, votes=len(by_species[winner]), total=total)
