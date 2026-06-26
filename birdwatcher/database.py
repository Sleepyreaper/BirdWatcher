"""SQLite storage + the queries that power the weekly grid."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    species       TEXT NOT NULL,
    confidence    REAL NOT NULL,
    image_path    TEXT,
    detector_conf REAL
);
CREATE INDEX IF NOT EXISTS idx_sightings_ts ON sightings(ts);
CREATE INDEX IF NOT EXISTS idx_sightings_species ON sightings(species);
"""


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- writes -----------------------------------------------------------
    def add_sighting(
        self,
        species: str,
        confidence: float,
        image_path: str | None = None,
        detector_conf: float | None = None,
        ts: datetime | None = None,
    ) -> int:
        ts = ts or datetime.now()
        cur = self._conn.execute(
            "INSERT INTO sightings (ts, species, confidence, image_path, detector_conf)"
            " VALUES (?, ?, ?, ?, ?)",
            (ts.isoformat(timespec="seconds"), species, confidence, image_path, detector_conf),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def last_seen(self, species: str) -> datetime | None:
        """Most recent sighting time for a species (used for visit debounce)."""
        row = self._conn.execute(
            "SELECT ts FROM sightings WHERE species = ? ORDER BY ts DESC LIMIT 1",
            (species,),
        ).fetchone()
        return datetime.fromisoformat(row["ts"]) if row else None

    # --- reads for the UI -------------------------------------------------
    def week_grid(self, week_start: date) -> dict:
        """Build the weekly grid payload (Sun..Sat) for the web UI.

        Returns:
            {
              "start": "2026-06-21",
              "days": ["2026-06-21", ... 7 ISO dates ...],
              "species": [
                 {"name", "thumb", "total", "counts": [7 ints], "times": [[..],..7]}
              ]
            }
        """
        week_end = week_start + timedelta(days=7)
        rows = self._conn.execute(
            "SELECT ts, species, image_path, confidence FROM sightings"
            " WHERE ts >= ? AND ts < ? ORDER BY ts ASC",
            (week_start.isoformat(), week_end.isoformat()),
        ).fetchall()

        # species -> day_index(0..6) -> list of (time_str, image_path, conf)
        agg: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            ts = datetime.fromisoformat(r["ts"])
            day_idx = (ts.date() - week_start).days
            if 0 <= day_idx < 7:
                agg[r["species"]][day_idx].append(
                    (ts.strftime("%H:%M"), r["image_path"], r["confidence"])
                )

        species_payload = []
        for name, by_day in agg.items():
            counts = [len(by_day.get(i, [])) for i in range(7)]
            times = [[t[0] for t in by_day.get(i, [])] for i in range(7)]
            # best thumbnail = highest-confidence crop across the week
            best = max(
                (item for day in by_day.values() for item in day),
                key=lambda x: x[2],
                default=(None, None, 0),
            )
            species_payload.append(
                {
                    "name": name,
                    "thumb": best[1],
                    "total": sum(counts),
                    "counts": counts,
                    "times": times,
                }
            )

        species_payload.sort(key=lambda s: s["total"], reverse=True)

        return {
            "start": week_start.isoformat(),
            "days": [(week_start + timedelta(days=i)).isoformat() for i in range(7)],
            "species": species_payload,
        }


def week_start_for(d: date) -> date:
    """Sunday that begins the week containing date d (Sun=start, Sat=end)."""
    # Python weekday(): Mon=0..Sun=6. We want the most recent Sunday.
    return d - timedelta(days=(d.weekday() + 1) % 7)
