"""Flask app: serves the weekly bird grid, the species catalog, and images.

Merges two sources: visual visits (our pipeline -> SQLite) and acoustic
detections (BirdNET-Go's SQLite, read-only). A species can be seen, heard, or
both — "both" means confirmed at the feeder, not just in earshot.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

from ..birdnetgo import BirdnetGoReader
from ..config import PROJECT_ROOT, Config, load_config
from ..database import Database, week_start_for
from ..weather import hourly_weather

DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
REFERENCE_DIR = PROJECT_ROOT / "assets" / "reference"
MAX_INGEST_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_INGEST_KEYS = {
    "token", "species", "confidence", "detector_conf",
    "first_ts", "last_ts", "frames", "image_b64",
}


def _ref_url(reference_image: str | None) -> str | None:
    if not reference_image:
        return None
    return "/reference/" + reference_image.rsplit("/", 1)[-1]


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-").replace("'", "").replace("/", "-")


def _load_catalog() -> tuple[dict, dict]:
    path = PROJECT_ROOT / "data" / "species_catalog.json"
    if not path.exists():
        return {"name": "", "ebird_code": ""}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    by_name = {sp["common_name"]: sp for sp in data.get("species", [])}
    return data.get("region", {}), by_name


def _load_birdnet_labels() -> dict[str, str]:
    """Scientific -> common for off-catalog species BirdNET-Go hears (its DB
    stores only Latin names). Missing entries fall back to the scientific name."""
    path = PROJECT_ROOT / "data" / "birdnet_labels.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _load_critters() -> dict[str, dict]:
    """common_name -> {common_name, scientific_name} for non-bird wildlife.
    Any sighting whose species is in this map is filed under 'Critters'."""
    path = PROJECT_ROOT / "data" / "critters.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {c["common_name"]: c for c in data.get("critters", [])}


def _json_error(code: str, status: int = 400):
    return jsonify({"ok": False, "error": code}), status


def _safe_send(root: Path, rel: str):
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return _json_error("not_found", 404)
    if not candidate.exists() or not candidate.is_file():
        return _json_error("not_found", 404)
    return send_from_directory(root, rel)


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _validate_ingest_payload(data: dict) -> tuple[dict | None, tuple | None]:
    extra = set(data) - ALLOWED_INGEST_KEYS
    if extra:
        return None, _json_error("unexpected_fields")

    required = [
        "token", "species", "confidence", "detector_conf",
        "first_ts", "last_ts", "frames", "image_b64",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        return None, _json_error("missing_fields")

    species = data.get("species")
    first_ts = _parse_iso(data.get("first_ts"))
    last_ts = _parse_iso(data.get("last_ts"))
    image_b64 = data.get("image_b64")
    token = data.get("token", "")

    if not isinstance(species, str) or not species.strip():
        return None, _json_error("invalid_species")
    if first_ts is None or last_ts is None:
        return None, _json_error("invalid_timestamp")
    if last_ts < first_ts:
        return None, _json_error("invalid_timestamp_order")
    if not isinstance(image_b64, str):
        return None, _json_error("invalid_image")
    if not isinstance(token, str):
        return None, _json_error("invalid_token")

    try:
        confidence = float(data.get("confidence"))
        detector_conf = float(data.get("detector_conf"))
    except (TypeError, ValueError):
        return None, _json_error("invalid_confidence")
    try:
        frames = int(data.get("frames"))
    except (TypeError, ValueError):
        return None, _json_error("invalid_frames")

    if not (0.0 <= confidence <= 1.0) or not (0.0 <= detector_conf <= 1.0):
        return None, _json_error("invalid_confidence")
    if frames < 0:
        return None, _json_error("invalid_frames")
    if len(image_b64.encode("utf-8")) > MAX_INGEST_BYTES:
        return None, _json_error("image_too_large", 413)

    return {
        "token": token,
        "species": species.strip(),
        "confidence": confidence,
        "detector_conf": detector_conf,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "frames": frames,
        "image_b64": image_b64,
    }, None


def _atomic_write_bytes(dest: Path, payload: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=dest.suffix, dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or load_config()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_INGEST_BYTES + 262144
    captures_dir = cfg.paths.captures_path()
    library_dir = captures_dir.parent / "library"
    region, catalog = _load_catalog()
    sci_to_common = {
        sp["scientific_name"]: name
        for name, sp in catalog.items()
        if sp.get("scientific_name")
    }
    birdnet_labels = _load_birdnet_labels()
    critters = _load_critters()   # common_name -> meta; these file under "Critters"
    birdnet = BirdnetGoReader(cfg.audio.birdnet_db)
    # Reference photos already on disk — lets off-catalog heard species (hawks,
    # cicadas, frogs) show a picture if tools/fetch_heard_images.py fetched one,
    # saved as <scientific-slug>.jpg. Scanned once at startup.
    ref_files = {p.name for p in REFERENCE_DIR.glob("*.jpg")} if REFERENCE_DIR.exists() else set()

    def _sci_ref(sci: str) -> str | None:
        fn = _slug(sci) + ".jpg"
        return ("/reference/" + fn) if fn in ref_files else None

    def _heard_display(sci: str) -> dict:
        """Resolve a scientific name to display fields. Catalog species get
        their common name + reference photo; off-list species use the BirdNET
        label map for a real name and a fetched photo if we have one, then fall
        back to the raw scientific name."""
        common = sci_to_common.get(sci)
        if common:
            meta = catalog.get(common, {})
            return {
                "name": common,
                "scientific": meta.get("scientific_name") or sci,
                "family": meta.get("family"),
                "reference": _ref_url(meta.get("reference_image")),
                "off_list": False,
            }
        return {
            "name": birdnet_labels.get(sci, sci),
            "scientific": sci,
            "family": None,
            "reference": _sci_ref(sci),
            "off_list": True,
        }

    def get_db() -> Database:
        if not hasattr(app, "_bw_db"):
            app._bw_db = Database(cfg.paths.db_path())
        return app._bw_db

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/review")
    def review():
        return render_template("review.html")

    @app.route("/species/<path:name>")
    def species_page(name):
        return render_template("species.html", name=name)

    @app.route("/api/species/<path:name>")
    def api_species(name):
        detail = get_db().species_detail(name)
        if name in critters:
            sci = critters[name].get("scientific_name")
            meta = {"scientific": sci, "family": None,
                    "reference": _sci_ref(sci) if sci else None, "kind": "critter"}
        else:
            cm = catalog.get(name, {})
            sci = cm.get("scientific_name")
            ref = _ref_url(cm.get("reference_image")) or (_sci_ref(sci) if sci else None)
            meta = {"scientific": sci, "family": cm.get("family"),
                    "reference": ref, "kind": "bird"}
        heard = birdnet.heard_total(sci) if sci else 0
        return jsonify({**detail, **meta, "heard_total": heard,
                        "audio_on": birdnet.available(), "region": region})

    @app.route("/api/week")
    def api_week():
        start_param = request.args.get("start")
        week_start = date.fromisoformat(start_param) if start_param else week_start_for(date.today())
        grid = get_db().week_grid(week_start)
        # Every species heard this week, keyed by scientific name (unfiltered),
        # so the dashboard mirrors BirdNET-Go instead of only the 32 catalog birds.
        try:
            heard_all = birdnet.heard_week_all(week_start)
        except Exception:
            heard_all = {}

        # Catalog-name overlay {common: [7]} for the 🔊 marks on seen rows and
        # the "heard" flag in the catalog list — derived from the same query.
        heard = {}
        for sci, counts in heard_all.items():
            common = sci_to_common.get(sci)
            if not common:
                continue
            prev = heard.get(common)
            heard[common] = [a + b for a, b in zip(prev, counts)] if prev else list(counts)

        seen, critter_rows, seen_names = [], [], set()
        per_day = [0] * 7
        critter_day = [0] * 7
        for sp in grid["species"]:
            if sp["name"] in critters:
                cmeta = critters[sp["name"]]
                sci = cmeta.get("scientific_name")
                critter_rows.append({
                    **sp,
                    "scientific": sci,
                    "reference": _sci_ref(sci) if sci else None,
                })
                for i, c in enumerate(sp["counts"]):
                    critter_day[i] += c
                continue
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

        # Every species heard this week, with daily counts — including birds also
        # seen at the feeder (e.g. the Carolina Wren), which the seen grid only
        # marks with a 🔊 and never showed a heard count for. `also_seen` lets the
        # UI flag the overlap.
        heard_only = []
        for sci, counts in heard_all.items():
            disp = _heard_display(sci)
            heard_only.append({
                **disp,
                "heard": counts,
                "also_seen": (not disp["off_list"]) and disp["name"] in seen_names,
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
            "critters": critter_rows,
            "heard_only": heard_only,
            "catalog": catalog_list,
            "audio_on": birdnet.available(),
            "stats": {
                "visits": total,
                "species_seen": len(seen),
                "species_heard": len(heard_all),
                "critters": len(critter_rows),
                "critter_visits": sum(critter_day),
                "on_list": len(catalog_list),
                "busiest_day": DAYS[per_day.index(max(per_day))] if total else "—",
            },
        })

    @app.route("/api/day")
    def api_day():
        dparam = request.args.get("date")
        day = date.fromisoformat(dparam) if dparam else date.today()
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

        # Heard-by-hour (acoustic) — the bottom grid, mirroring BirdNET-Go.
        try:
            heard_raw = birdnet.heard_day_hours(day)
        except Exception:
            heard_raw = {}
        heard = []
        for sci, counts in heard_raw.items():
            disp = _heard_display(sci)
            heard.append({**disp, "total": sum(counts), "counts": counts})
        heard.sort(key=lambda s: s["total"], reverse=True)

        wx = []
        if cfg.weather.enabled:
            try:
                wx = hourly_weather(day, cfg.weather.latitude, cfg.weather.longitude)
            except Exception:
                wx = []

        total = sum(per_hour)
        busiest = per_hour.index(max(per_hour)) if total else None
        return jsonify({
            "date": day.isoformat(),
            "hours": grid["hours"],
            "species": species,
            "heard": heard,
            "weather": wx,
            "region": region,
            "is_today": day == date.today(),
            "stats": {
                "visits": total,
                "species_seen": len(species),
                "species_heard": len(heard),
                "busiest_hour": (f"{busiest % 12 or 12}{'am' if busiest < 12 else 'pm'}" if busiest is not None else "—"),
            },
        })

    @app.route("/api/recent")
    def api_recent():
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
        try:
            heard_items = birdnet.recent(sci_to_common, limit)
        except Exception:
            heard_items = []
        for h in heard_items:
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
        return _safe_send(captures_dir, rel)

    @app.route("/reference/<path:rel>")
    def reference(rel: str):
        return _safe_send(REFERENCE_DIR, rel)

    @app.route("/library/<path:rel>")
    def library(rel: str):
        return _safe_send(library_dir, rel)

    @app.route("/api/ingest", methods=["POST"])
    def ingest():
        if request.content_type is None or "application/json" not in request.content_type.lower():
            return _json_error("content_type_required", 415)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _json_error("invalid_json")

        payload, err = _validate_ingest_payload(data)
        if err:
            return err

        expected = cfg.pipeline.ingest_token or ""
        if expected and payload["token"] != expected:
            return _json_error("invalid_token", 403)

        try:
            image_bytes = base64.b64decode(payload["image_b64"], validate=True)
        except (binascii.Error, ValueError):
            return _json_error("invalid_image")
        if not image_bytes:
            return _json_error("invalid_image")
        if len(image_bytes) > MAX_IMAGE_BYTES:
            return _json_error("image_too_large", 413)

        day_dir = captures_dir / payload["first_ts"].strftime("%Y-%m-%d")
        slug = payload["species"].lower().replace(" ", "-").replace("/", "-")
        out = day_dir / f"{slug}_{payload['first_ts'].strftime('%H%M%S')}.jpg"
        try:
            _atomic_write_bytes(out, image_bytes)
            rel = str(out.relative_to(captures_dir)).replace("\\", "/")
            get_db().add_visit(
                species=payload["species"],
                confidence=payload["confidence"],
                image_path=rel,
                detector_conf=payload["detector_conf"],
                first_ts=payload["first_ts"],
                last_ts=payload["last_ts"],
                frames=payload["frames"],
            )
        except Exception as e:
            print(f"[web] ingest failed: {type(e).__name__}")
            return _json_error("ingest_failed", 500)
        return jsonify({"ok": True})

    @app.route("/api/unverified")
    def api_unverified():
        limit = int(request.args.get("limit", 40))
        visits = get_db().list_unverified(limit)
        verified, total = get_db().review_counts()
        library = get_db().library_counts()
        species = [{
            "name": name,
            "reference": _ref_url(meta.get("reference_image")),
        } for name, meta in catalog.items()]
        return jsonify({
            "visits": visits,
            "progress": {"verified": verified, "total": total},
            "library": library,
            "species": species,
        })

    @app.route("/api/verify", methods=["POST"])
    def api_verify():
        data = request.get_json(silent=True) or {}
        try:
            sighting_id = int(data.get("id"))
            species = str(data.get("species") or "").strip()
        except (TypeError, ValueError):
            return _json_error("invalid_request")
        if not species:
            return _json_error("invalid_species")
        get_db().set_verified(sighting_id, species)
        return jsonify({"ok": True})

    @app.route("/api/library", methods=["POST"])
    def api_library():
        data = request.get_json(silent=True) or {}
        try:
            sighting_id = int(data.get("id"))
            species = str(data.get("species") or "").strip()
            image_path = str(data.get("image_path") or "").strip()
        except (TypeError, ValueError):
            return _json_error("invalid_request")
        if not species or not image_path:
            return _json_error("invalid_request")
        get_db().set_verified(sighting_id, species)
        get_db().add_library_example(species, image_path, sighting_id=sighting_id)
        return jsonify({"ok": True, "count": get_db().library_counts().get(species, 0)})

    @app.route("/api/reject", methods=["POST"])
    def api_reject():
        data = request.get_json(silent=True) or {}
        try:
            sighting_id = int(data.get("id"))
        except (TypeError, ValueError):
            return _json_error("invalid_request")
        get_db().reject(sighting_id)
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    cfg = load_config()
    app = create_app(cfg)
    app.run(host=cfg.web.host, port=cfg.web.port, debug=cfg.web.debug)
