"""Flask app: serves the weekly bird grid, the species catalog, and images."""

from __future__ import annotations

import json
from datetime import date, timedelta

from flask import Flask, jsonify, render_template, request, send_from_directory

from ..config import PROJECT_ROOT, Config, load_config
from ..database import Database, week_start_for

DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
REFERENCE_DIR = PROJECT_ROOT / "assets" / "reference"


def _ref_url(reference_image: str | None) -> str | None:
    """Turn a catalog reference_image path into a served URL."""
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
    captures_dir = cfg.paths.captures_path()
    region, catalog = _load_catalog()

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
        week_start = (
            date.fromisoformat(start_param) if start_param else week_start_for(date.today())
        )
        grid = get_db().week_grid(week_start)

        seen, seen_names = [], set()
        per_day = [0] * 7
        for sp in grid["species"]:
            meta = catalog.get(sp["name"], {})
            seen.append({
                **sp,
                "scientific": meta.get("scientific_name"),
                "family": meta.get("family"),
                "reference": _ref_url(meta.get("reference_image")),
            })
            seen_names.add(sp["name"])
            for i, c in enumerate(sp["counts"]):
                per_day[i] += c

        catalog_list = [{
            "name": sp["common_name"],
            "scientific": sp.get("scientific_name"),
            "family": sp.get("family"),
            "seasonality": sp.get("seasonality"),
            "reference": _ref_url(sp.get("reference_image")),
            "seen": sp["common_name"] in seen_names,
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
            "catalog": catalog_list,
            "stats": {
                "visits": total,
                "species_seen": len(seen),
                "on_list": len(catalog_list),
                "busiest_day": DAYS[per_day.index(max(per_day))] if total else "—",
            },
        })

    @app.route("/captures/<path:rel>")
    def captures(rel: str):
        return send_from_directory(captures_dir, rel)

    @app.route("/reference/<path:rel>")
    def reference(rel: str):
        return send_from_directory(REFERENCE_DIR, rel)

    return app


def main() -> None:
    cfg = load_config()
    app = create_app(cfg)
    print(f"BirdWatcher UI -> http://{cfg.web.host}:{cfg.web.port}")
    app.run(host=cfg.web.host, port=cfg.web.port, debug=cfg.web.debug)


if __name__ == "__main__":
    main()
