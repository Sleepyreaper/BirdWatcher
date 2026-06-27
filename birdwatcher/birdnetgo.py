"""Read-only reader for BirdNET-Go's SQLite DB (acoustic detections).

Maps BirdNET-Go detections (by scientific name) onto our species catalog so the
dashboard can show what was *heard* alongside what was *seen*. Opens the DB
read-only so it never interferes with BirdNET-Go's writes; any error yields an
empty result (the audio layer simply doesn't appear).

BirdNET-Go schema (relevant bits):
  detections(detected_at INTEGER epoch, confidence, label_id, unlikely, ...)
  labels(id, scientific_name, ...)
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path


class BirdnetGoReader:
    def __init__(self, db_path: str | Path | None):
        self.db_path = Path(db_path) if db_path else None

    def available(self) -> bool:
        return bool(self.db_path) and self.db_path.exists()

    def heard_week(self, week_start: date, sci_to_common: dict[str, str]) -> dict[str, list[int]]:
        """Return {catalog common_name: [7 daily heard-counts]} for the week.

        Only species present in our catalog (via sci_to_common) are kept.
        """
        if not self.available():
            return {}
        start = datetime.combine(week_start, time.min)
        end = start + timedelta(days=7)
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2)
            rows = con.execute(
                "SELECT d.detected_at, l.scientific_name FROM detections d "
                "JOIN labels l ON l.id = d.label_id "
                "WHERE d.detected_at >= ? AND d.detected_at < ? "
                "AND COALESCE(d.unlikely, 0) = 0",
                (int(start.timestamp()), int(end.timestamp())),
            ).fetchall()
            con.close()
        except Exception:
            return {}

        out: dict[str, list[int]] = {}
        for epoch, sci in rows:
            common = sci_to_common.get(sci)
            if not common:
                continue
            day = (datetime.fromtimestamp(epoch).date() - week_start).days
            if 0 <= day < 7:
                out.setdefault(common, [0] * 7)[day] += 1
        return out
