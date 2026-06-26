#!/usr/bin/env python
"""Fetch one reference image per species in data/species_catalog.json.

Pulls the lead photo from Wikipedia (Wikimedia Commons), saves it to
assets/reference/<slug>.jpg, and writes the local path back into the catalog.
Re-runnable: skips species that already have an image.

    python tools/fetch_reference_images.py

Be a good citizen: sets a User-Agent and sleeps between requests. Verify each
image's license on Commons before redistributing beyond personal use.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "species_catalog.json"
OUT_DIR = ROOT / "assets" / "reference"
UA = {"User-Agent": "BirdWatcher/0.1 (personal hobby project)"}


def slug(name: str) -> str:
    return name.lower().replace(" ", "-").replace("'", "").replace("/", "-")


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as fh:
        return json.loads(fh.read().decode())


def summary_image(title: str) -> str | None:
    """Return the lead image URL for a Wikipedia page title, or None."""
    url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(
        title.replace(" ", "_")
    )
    try:
        data = fetch_json(url)
    except Exception:
        return None
    img = data.get("thumbnail") or data.get("originalimage")
    return img["source"] if img else None


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    changed = 0

    for sp in catalog["species"]:
        if sp.get("reference_image"):
            continue
        # try common name first, then scientific name
        img_url = summary_image(sp["common_name"]) or summary_image(sp["scientific_name"])
        if not img_url:
            print(f"  ! no image found for {sp['common_name']}")
            continue
        dest = OUT_DIR / f"{slug(sp['common_name'])}.jpg"
        try:
            req = urllib.request.Request(img_url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as fh:
                dest.write_bytes(fh.read())
        except Exception as e:
            print(f"  ! download failed for {sp['common_name']}: {e}")
            continue
        sp["reference_image"] = str(dest.relative_to(ROOT)).replace("\\", "/")
        changed += 1
        print(f"  + {sp['common_name']} -> {dest.name}")
        time.sleep(1.0)  # be gentle with Wikimedia

    if changed:
        CATALOG.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print(f"done; fetched {changed} new image(s).")


if __name__ == "__main__":
    main()
