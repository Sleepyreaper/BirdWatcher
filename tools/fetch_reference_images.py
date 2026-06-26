#!/usr/bin/env python
"""Fetch one reference image per species in data/species_catalog.json.

Pulls each species' lead photo from Wikipedia (Wikimedia Commons) via the
pageimages API, saves it to assets/reference/<slug>.jpg, and writes the local
path back into the catalog. Re-runnable: skips species that already have an image.

    python tools/fetch_reference_images.py

Uses a Wikimedia-compliant User-Agent (with contact) and polite delays + 429
backoff so we don't get throttled. Verify each image's license on Commons before
redistributing beyond personal use.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "species_catalog.json"
OUT_DIR = ROOT / "assets" / "reference"
UA = {"User-Agent": "BirdWatcherBot/0.1 (https://github.com/Sleepyreaper/BirdWatcher; sleepyreaper@gmail.com)"}


def slug(name: str) -> str:
    return name.lower().replace(" ", "-").replace("'", "").replace("/", "-")


def _fetch(url: str, tries: int = 4) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as fh:
                return fh.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < tries - 1:
                time.sleep(5 * (i + 1))  # back off and retry
                continue
            raise
    raise RuntimeError("unreachable")


def page_image(title: str) -> str | None:
    """Lead image URL for a Wikipedia article via pageimages (follows redirects)."""
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
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    changed = 0

    for sp in catalog["species"]:
        if sp.get("reference_image"):
            continue
        img_url = page_image(sp["common_name"])
        if not img_url:
            time.sleep(1.5)
            img_url = page_image(sp["scientific_name"])
        if not img_url:
            print(f"  ! no image for {sp['common_name']}")
            time.sleep(1.5)
            continue
        dest = OUT_DIR / f"{slug(sp['common_name'])}.jpg"
        try:
            dest.write_bytes(_fetch(img_url))
        except Exception as e:
            print(f"  ! download failed for {sp['common_name']}: {e}")
            time.sleep(1.5)
            continue
        sp["reference_image"] = str(dest.relative_to(ROOT)).replace("\\", "/")
        changed += 1
        print(f"  + {sp['common_name']} -> {dest.name}")
        time.sleep(1.5)  # be gentle with Wikimedia

    if changed:
        CATALOG.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print(f"done; fetched {changed} new image(s).")


if __name__ == "__main__":
    main()
