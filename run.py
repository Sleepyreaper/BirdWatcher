#!/usr/bin/env python
"""BirdWatcher entrypoint.

    python run.py initdb     # create the SQLite DB
    python run.py watch      # run the capture->detect->classify pipeline
    python run.py web        # serve the weekly web UI
    python run.py seed       # insert fake sightings so you can preview the UI
"""

from __future__ import annotations

import argparse
import sys

from birdwatcher.config import load_config


def cmd_initdb(args) -> None:
    from birdwatcher.database import Database

    cfg = load_config(args.config)
    db = Database(cfg.paths.db_path())
    print(f"Initialized DB at {cfg.paths.db_path()}")
    db.close()


def cmd_watch(args) -> None:
    from birdwatcher.pipeline import Pipeline

    cfg = load_config(args.config)
    Pipeline(cfg).run()


def cmd_web(args) -> None:
    from birdwatcher.web.app import create_app

    cfg = load_config(args.config)
    app = create_app(cfg)
    print(f"BirdWatcher UI -> http://{cfg.web.host}:{cfg.web.port}")
    app.run(host=cfg.web.host, port=cfg.web.port, debug=cfg.web.debug)


def cmd_seed(args) -> None:
    """Populate this week with random sightings to preview the UI (no camera)."""
    import random
    from datetime import datetime, timedelta

    from birdwatcher.database import Database, week_start_for

    cfg = load_config(args.config)
    db = Database(cfg.paths.db_path())
    species = [
        "Northern Cardinal", "Black-capped Chickadee", "American Goldfinch",
        "Tufted Titmouse", "Downy Woodpecker", "House Finch", "Blue Jay",
        "White-breasted Nuthatch", "Mourning Dove", "Dark-eyed Junco",
    ]
    start = datetime.combine(week_start_for(datetime.today().date()), datetime.min.time())
    n = 0
    for day in range(7):
        for sp in species:
            if random.random() < 0.55:
                continue
            for _ in range(random.randint(1, 12)):
                ts = start + timedelta(
                    days=day, hours=random.randint(6, 19), minutes=random.randint(0, 59)
                )
                db.add_sighting(sp, round(random.uniform(0.4, 0.99), 2), ts=ts)
                n += 1
    db.close()
    print(f"Seeded {n} fake sightings for the current week. Run `python run.py web`.")


def cmd_test(args) -> None:
    """Connect to the RTSP stream and save one frame, to verify the camera."""
    try:
        import cv2
    except ImportError:
        print("opencv-python not installed. Run: pip install opencv-python numpy")
        return
    from datetime import datetime

    from birdwatcher.capture import RTSPCamera

    cfg = load_config(args.config)
    print(f"Connecting to {cfg.camera.rtsp_url} ...")
    cam = RTSPCamera(cfg.camera, cfg.motion)
    frame = cam.grab_one()
    if frame is None:
        print("FAILED: no frame received. Check the URL / that RTSP is enabled, "
              "and try dropping '?enableSrtp' (SRTP is often unsupported by FFmpeg).")
        return
    out = cfg.paths.captures_path() / f"_test_{datetime.now():%H%M%S}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)
    h, w = frame.shape[:2]
    print(f"OK: received {w}x{h} frame, saved {out}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BirdWatcher")
    parser.add_argument("-c", "--config", default=None, help="path to config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in [
        ("initdb", cmd_initdb), ("watch", cmd_watch),
        ("web", cmd_web), ("seed", cmd_seed), ("test", cmd_test),
    ]:
        p = sub.add_parser(name)
        p.set_defaults(func=fn)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
