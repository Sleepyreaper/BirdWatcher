#!/usr/bin/env python
"""Fetch a photo for each off-catalog species in data/birdnet_labels.json.

BirdNET-Go hears plenty of species (extra birds, cicadas, frogs) that aren't in
our 32-bird catalog, so they have no reference photo and show as an initials
badge. This pulls one image per label from Wikipedia and saves it to
assets/reference/<scientific-slug>.jpg — the same folder the app serves, keyed
by scientific name so web/app.py's `_sci_ref` picks it up automatically.

    python tools/fetch_heard_images.py

Re-runnable: skips species that already have an image. Wikimedia-compliant
User-Agent + polite delays + 429 backoff, same as fetch_reference_images.py.
Verify each image's license on Commons before redistributing beyond personal use.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "data" / "birdnet_labels.json"
OUT_DIR = ROOT / "assets" / "reference"
UA = {"User-Agent": "BirdWatcherBot/0.1 (https://github.com/Sleepyreaper/BirdWatcher; sleepyreaper@gmail.com)"}


def slug(name: str) -> str:
    return name.lower().replace(" ", "-").replace("'", "").replace("/", "-")


def search_name(common: str) -> str:
    """Drop the '(insect)'/'(amphibian)' hint before searching Wikipedia."""
    return common.split("(")[0].strip()


def _fetch(url: str, tries: int = 4) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as fh:
                return fh.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def page_image(title: str) -> str | None:
    api = (
        "https://en.wikipedia.org/w/api.php?action=query&format=json&redirects=1"
        "&prop=pageimages&piprop=original%7Cthumbnail&pithumbsize=500&titles="
        + urllib.parse.quote(title)
    )
    try:
        data = json.loads(_fetch(api).decode())
    except Exception:
        return None
    for page in data.get("query", {}).get("pages", {}).values():
        img = page.get("thumbnail") or page.get("original")
        if img:
            return img["source"]
    return None


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    fetched = skipped = missed = 0

    for sci, common in labels.items():
        if sci.startswith("_"):
            continue
        dest = OUT_DIR / f"{slug(sci)}.jpg"
        if dest.exists():
            skipped += 1
            continue
        # common name first (better Wikipedia hit), then scientific
        img_url = page_image(search_name(common)) or (time.sleep(1.2) or page_image(sci))
        if not img_url:
            print(f"  ! no image for {common} ({sci})")
            missed += 1
            time.sleep(1.2)
            continue
        try:
            dest.write_bytes(_fetch(img_url))
            fetched += 1
            print(f"  + {common} -> {dest.name}")
        except Exception as e:
            print(f"  ! download failed for {common}: {e}")
            missed += 1
        time.sleep(1.5)  # be gentle with Wikimedia

    print(f"done; fetched {fetched}, skipped {skipped} existing, missed {missed}.")


if __name__ == "__main__":
    main()
