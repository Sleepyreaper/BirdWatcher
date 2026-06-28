"""Flask app: serves the weekly bird grid, the species catalog, and images.

Merges two sources: visual visits (our pipeline -> SQLite) and acoustic
detections (BirdNET-Go's SQLite, read-only). A species can be seen, heard, or
both — "both" means confirmed at the feeder, not just in earshot.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import re
from datetime import date, timedelta

from flask import Flask, jsonify, render_template, request, send_from_directory

from ..birdnetgo import BirdnetGoReader
from ..config import PROJECT_ROOT, Config, load_config
from ..database import Database, week_start_for
from ..weather import hourly_weather

DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
REFERENCE_DIR = PROJECT_ROOT / "assets" / "reference"

# Filename-safe slug: lowercase, only [a-z0-9-], collapsed. Used for the
# server-generated capture filename so a hostile `species` value can never inject
# path separators, "..", or other surprises into the write path.
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _safe_slug(name: str, *, fallback: str = "bird", max_len: int = 40) -> str:
    slug = _SLUG_RE.sub("-", (name or "").lower()).strip("-")
    return (slug[:max_len].strip("-")) or fallback


def _parse_date_arg(value: str | None, default: date) -> date | None:
    """Parse a YYYY-MM-DD query arg. Returns default when absent, None when
    present-but-invalid (so the caller can emit a clean 400 instead of a 500)."""
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _ref_url(reference_image: str | None) -> str | None:
    if not reference_image:
        return None
    return "/reference/" + reference_image.rsplit("/", 1)[-1]


def _load_catalog() -> tuple[dict, dict]:
    path = PROJECT_ROOT / "data" / "species_catalog.json"
    if not path.exists():
        return {"name": "", "ebird_code": ""}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    by_name = {sp["common_name"]: sp for sp in data.get("species", [])}
    return data.get("region", {}), by_name


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or load_config()
    app = Flask(__name__)
    # Reject oversized request bodies up front (returns 413). Protects the
    # base64 image upload path on /api/ingest from memory-exhaustion on the Pi.
    app.config["MAX_CONTENT_LENGTH"] = cfg.web.max_content_length
    captures_dir = cfg.paths.captures_path()
    region, catalog = _load_catalog()
    sci_to_common = {
        sp["scientific_name"]: name
        for name, sp in catalog.items()
        if sp.get("scientific_name")
    }
    birdnet = BirdnetGoReader(cfg.audio.birdnet_db)

    def get_db() -> Database:
        if not hasattr(app, "_bw_db"):
            app._bw_db = Database(cfg.paths.db_path())
        return app._bw_db

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/week")
    def api_week():
        start_param = request.args.get("start")
        week_start = _parse_date_arg(start_param, week_start_for(date.today()))
        if week_start is None:
            return jsonify({"error": "bad 'start' date — expected YYYY-MM-DD"}), 400
        grid = get_db().week_grid(week_start)
        heard = birdnet.heard_week(week_start, sci_to_common)

        seen, seen_names = [], set()
        per_day = [0] * 7
        for sp in grid["species"]:
            meta = catalog.get(sp["name"], {})
            seen.append({
                **sp,
                "scientific": meta.get("scientific_name"),
                "family": meta.get("family"),
                "reference": _ref_url(meta.get("reference_image")),
                "heard": heard.get(sp["name"], [0] * 7),
            })
            seen_names.add(sp["name"])
            for i, c in enumerate(sp["counts"]):
                per_day[i] += c

        # catalog species heard but NOT seen at the feeder this week
        heard_only = []
        for name, counts in heard.items():
            if name in seen_names:
                continue
            meta = catalog.get(name, {})
            heard_only.append({
                "name": name,
                "scientific": meta.get("scientific_name"),
                "family": meta.get("family"),
                "reference": _ref_url(meta.get("reference_image")),
                "heard": counts,
            })
        heard_only.sort(key=lambda s: sum(s["heard"]), reverse=True)

        catalog_list = [{
            "name": sp["common_name"],
            "scientific": sp.get("scientific_name"),
            "family": sp.get("family"),
            "seasonality": sp.get("seasonality"),
            "reference": _ref_url(sp.get("reference_image")),
            "seen": sp["common_name"] in seen_names,
            "heard": sp["common_name"] in heard,
        } for sp in catalog.values()]

        total = sum(per_day)
        return jsonify({
            "region": region,
            "start": grid["start"],
            "days": grid["days"],
            "prev": (week_start - timedelta(days=7)).isoformat(),
            "next": (week_start + timedelta(days=7)).isoformat(),
            "is_current": week_start == week_start_for(date.today()),
            "seen": seen,
            "heard_only": heard_only,
            "catalog": catalog_list,
            "audio_on": birdnet.available(),
            "stats": {
                "visits": total,
                "species_seen": len(seen),
                "species_heard": len(heard),
                "on_list": len(catalog_list),
                "busiest_day": DAYS[per_day.index(max(per_day))] if total else "—",
            },
        })

    @app.route("/api/day")
    def api_day():
        """Hourly drill-down for one day: species x 24h counts + a weather row."""
        dparam = request.args.get("date")
        day = _parse_date_arg(dparam, date.today())
        if day is None:
            return jsonify({"error": "bad 'date' — expected YYYY-MM-DD"}), 400
        grid = get_db().day_hours(day)

        species = []
        per_hour = [0] * 24
        for sp in grid["species"]:
            meta = catalog.get(sp["name"], {})
            species.append({
                **sp,
                "scientific": meta.get("scientific_name"),
                "reference": _ref_url(meta.get("reference_image")),
            })
            for h, c in enumerate(sp["counts"]):
                per_hour[h] += c

        wx = []
        if cfg.weather.enabled:
            wx = hourly_weather(day, cfg.weather.latitude, cfg.weather.longitude)

        total = sum(per_hour)
        busiest = per_hour.index(max(per_hour)) if total else None
        return jsonify({
            "date": day.isoformat(),
            "hours": grid["hours"],
            "species": species,
            "weather": wx,
            "region": region,
            "is_today": day == date.today(),
            "stats": {
                "visits": total,
                "species_seen": len(species),
                "busiest_hour": (f"{busiest % 12 or 12}{'am' if busiest < 12 else 'pm'}"
                                 if busiest is not None else "—"),
            },
        })

    @app.route("/api/recent")
    def api_recent():
        """Unified live feed: recent visits (seen, with photos) + heard detections."""
        limit = int(request.args.get("limit", 14))
        items = []
        for v in get_db().recent_visits(limit):
            meta = catalog.get(v["species"], {})
            items.append({
                "kind": "seen",
                "name": v["species"],
                "scientific": meta.get("scientific_name"),
                "reference": _ref_url(meta.get("reference_image")),
                "thumb": v["image_path"],
                "confidence": v["confidence"],
                "ts": v["last_ts"] or v["ts"],
            })
        for h in birdnet.recent(sci_to_common, limit):
            meta = catalog.get(h["name"], {})
            items.append({
                "kind": "heard",
                "name": h["name"],
                "scientific": h["scientific"],
                "reference": _ref_url(meta.get("reference_image")),
                "thumb": None,
                "confidence": h["confidence"],
                "ts": h["ts"],
            })
        items.sort(key=lambda x: x["ts"], reverse=True)
        return jsonify({"items": items[:limit], "audio_on": birdnet.available()})

    @app.route("/captures/<path:rel>")
    def captures(rel: str):
        return send_from_directory(captures_dir, rel)

    @app.route("/reference/<path:rel>")
    def reference(rel: str):
        return send_from_directory(REFERENCE_DIR, rel)

    @app.route("/api/ingest", methods=["POST"])
    def ingest():
        """Receive a visit (metadata + best crop) from a remote watcher (e.g. the PC)."""
        from datetime import datetime

        data = request.get_json(force=True, silent=True) or {}
        expected = cfg.pipeline.ingest_token
        # Constant-time compare so a LAN attacker can't brute-force the shared
        # secret by timing. Always compares to avoid an early-out timing oracle.
        if expected and not hmac.compare_digest(str(data.get("token", "")), str(expected)):
            return jsonify({"error": "forbidden"}), 403
        try:
            first = datetime.fromisoformat(data["first_ts"])
        except (KeyError, ValueError, TypeError):
            return jsonify({"error": "bad or missing first_ts"}), 400
        try:
            last = datetime.fromisoformat(data["last_ts"]) if data.get("last_ts") else first
        except (ValueError, TypeError):
            return jsonify({"error": "bad last_ts"}), 400
        species = str(data.get("species", "Unknown bird"))[:120]

        rel = None
        if data.get("image_b64"):
            try:
                raw = base64.b64decode(str(data["image_b64"]), validate=True)
            except (binascii.Error, ValueError):
                return jsonify({"error": "bad image_b64"}), 400
            if len(raw) > cfg.pipeline.max_image_bytes:
                return jsonify({"error": "image too large"}), 413
            day_dir = captures_dir / first.strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            # Server-generated, path-safe filename — never trust client input here.
            out = day_dir / f"{_safe_slug(species)}_{first.strftime('%H%M%S')}.jpg"
            out.write_bytes(raw)
            rel = str(out.relative_to(captures_dir)).replace("\\", "/")

        try:
            confidence = float(data.get("confidence", 0))
            frames = int(data.get("frames", 1))
        except (ValueError, TypeError):
            return jsonify({"error": "bad confidence or frames"}), 400

        get_db().add_visit(
            species=species,
            confidence=confidence,
            image_path=rel,
            detector_conf=data.get("detector_conf"),
            first_ts=first,
            last_ts=last,
            frames=frames,
        )
        return jsonify({"ok": True})

    # --- human-in-the-loop review ----------------------------------------
    @app.route("/review")
    def review():
        return render_template("review.html")

    @app.route("/api/unverified")
    def api_unverified():
        ver, total = get_db().review_counts()
        return jsonify({
            "visits": get_db().list_unverified(int(request.args.get("limit", 40))),
            "species": [
                {"name": n, "reference": _ref_url(sp.get("reference_image"))}
                for n, sp in sorted(catalog.items())
            ],
            "progress": {"verified": ver, "total": total},
        })

    @app.route("/api/verify", methods=["POST"])
    def api_verify():
        data = request.get_json(force=True, silent=True) or {}
        try:
            get_db().set_verified(int(data["id"]), str(data["species"]))
        except (KeyError, ValueError, TypeError):
            return jsonify({"error": "bad id or species"}), 400
        return jsonify({"ok": True})

    return app


def main() -> None:
    cfg = load_config()
    app = create_app(cfg)
    print(f"BirdWatcher UI -> http://{cfg.web.host}:{cfg.web.port}")
    app.run(host=cfg.web.host, port=cfg.web.port, debug=cfg.web.debug)


if __name__ == "__main__":
    main()
