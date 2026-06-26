"""Flask app: serves the weekly bird grid and its JSON API."""

from __future__ import annotations

from datetime import date, timedelta

from flask import Flask, jsonify, render_template, request, send_from_directory

from ..config import Config, load_config
from ..database import Database, week_start_for


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or load_config()
    app = Flask(__name__)
    captures_dir = cfg.paths.captures_path()

    def get_db() -> Database:
        # one connection per app; sqlite is set check_same_thread=False
        if not hasattr(app, "_bw_db"):
            app._bw_db = Database(cfg.paths.db_path())
        return app._bw_db

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/week")
    def api_week():
        start_param = request.args.get("start")
        if start_param:
            week_start = date.fromisoformat(start_param)
        else:
            week_start = week_start_for(date.today())
        payload = get_db().week_grid(week_start)
        payload["prev"] = (week_start - timedelta(days=7)).isoformat()
        payload["next"] = (week_start + timedelta(days=7)).isoformat()
        payload["is_current"] = week_start == week_start_for(date.today())
        return jsonify(payload)

    @app.route("/captures/<path:rel>")
    def captures(rel: str):
        return send_from_directory(captures_dir, rel)

    return app


def main() -> None:
    cfg = load_config()
    app = create_app(cfg)
    print(f"BirdWatcher UI → http://{cfg.web.host}:{cfg.web.port}")
    app.run(host=cfg.web.host, port=cfg.web.port, debug=cfg.web.debug)


if __name__ == "__main__":
    main()
