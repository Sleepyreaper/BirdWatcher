"""Bulk vision re-review — point the tiebreaker at the historical backlog.

Manual review doesn't scale: thousands of crops accumulated before voting and
the vision tiebreaker existed, and a lot of them are the same systematic BioCLIP
misread over and over (a creek raccoon logged 181 times as a "flying squirrel").
Nobody sorts that by hand.

So we let the machine sort the machine's mess. The same local vision model that
adjudicates live visits re-judges the old ones: for each unreviewed sighting it
looks at the saved crop, offers the vision model BioCLIP's top candidates for
that crop, and then re-labels it to the true species, confirms the existing
label, or (with reject_unsure) hides it. --dry-run prints the plan and writes
nothing, so you approve a one-page summary instead of eyeballing thousands.

Only touches unreviewed rows, so it's naturally resumable — re-run to continue.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from .classifier import build_classifier
from .config import Config
from .database import Database
from .vision import adjudicate as vision_adjudicate


def _candidates(classifier, crop, k: int) -> list[str]:
    """BioCLIP's top-k species for this crop, de-duped, order preserved — the
    shortlist the vision model chooses from (includes the current wrong label
    plus its runner-ups, so vision can pick the species BioCLIP missed)."""
    pool: list[str] = []
    for r in classifier.classify_topk(crop, k):
        if r.species not in pool:
            pool.append(r.species)
    return pool


def run_auto_review(cfg: Config, source: str | None = None,
                    species: list[str] | None = None, limit: int = 100000,
                    dry_run: bool = True, reject_unsure: bool = False,
                    k: int = 8) -> dict:
    """Re-judge unreviewed sightings with the vision model. Returns a stats dict
    and prints progress + a summary. With dry_run, nothing is written."""
    import cv2

    vcfg = replace(cfg.vision, enabled=True)   # force on for the review pass
    if not cfg.vision.enabled:
        print("[review-auto] note: vision is disabled in config; using its model "
              f"settings anyway ({vcfg.model} @ {vcfg.ollama_url}).")
    captures = cfg.paths.captures_path()
    db = Database(cfg.paths.db_path())
    if not cfg.classifier.library_dir:
        cfg.classifier.library_dir = str(captures.parent / "library")
    classifier = build_classifier(cfg.classifier)

    todo = db.sightings_to_review(source, species, limit)
    scope = (f" from {source}" if source else "") + (f" in {species}" if species else "")
    print(f"[review-auto] {'DRY-RUN — ' if dry_run else ''}re-judging "
          f"{len(todo)} unreviewed sighting(s){scope} with {vcfg.model} …")

    corrected: Counter = Counter()   # (old_species, new_species) -> count
    confirmed = unsure = errors = 0
    for i, s in enumerate(todo, 1):
        crop = cv2.imread(str(captures / s["image_path"]))
        if crop is None:
            errors += 1
            continue
        try:
            verdict = vision_adjudicate(vcfg, crop, _candidates(classifier, crop, k))
        except Exception as e:
            print(f"[review-auto] id={s['id']} failed: {e}")
            errors += 1
            continue
        current = s["species"]
        if verdict is None:
            unsure += 1
            if reject_unsure and not dry_run:
                db.reject(s["id"])
        elif verdict == current:
            confirmed += 1
            if not dry_run:
                db.set_verified(s["id"], verdict)
        else:
            corrected[(current, verdict)] += 1
            if not dry_run:
                db.set_verified(s["id"], verdict)
        if i % 25 == 0:
            print(f"[review-auto]   … {i}/{len(todo)}")

    db.close()

    relabeled = sum(corrected.values())
    print("\n===== review-auto summary "
          f"({'DRY RUN — nothing written' if dry_run else 'applied'}) =====")
    print(f"  scanned      {len(todo)}")
    print(f"  re-labeled   {relabeled}")
    for (old, new), n in corrected.most_common():
        print(f"       {old}  ->  {new}   ×{n}")
    print(f"  confirmed    {confirmed}   (vision agreed with the existing label)")
    if reject_unsure:
        print(f"  rejected     {unsure}   (vision couldn't ID — hidden)")
    else:
        print(f"  unsure       {unsure}   (left as-is; pass --reject-unsure to hide)")
    print(f"  errors       {errors}   (missing/unreadable crop)")
    if dry_run:
        print("\n  Dry run — re-run without --dry-run to apply.")

    return {"scanned": len(todo), "relabeled": relabeled, "confirmed": confirmed,
            "unsure": unsure, "errors": errors}
