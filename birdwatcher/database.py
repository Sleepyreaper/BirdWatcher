"""SQLite storage + the queries that power the weekly grid.

Each row in `sightings` is one *visit* (a bird present across one or more frames),
not one detection. The pipeline collapses frames into a visit and writes the best one.
"""

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
    detector_conf REAL,
    last_ts       TEXT,
    frames        INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_sightings_ts ON sightings(ts);
CREATE INDEX IF NOT EXISTS idx_sightings_species ON sightings(species);
"""

# Columns added after the first release; applied to existing DBs at startup.
_MIGRATIONS = [("last_ts", "TEXT"), ("frames", "INTEGER DEFAULT 1")]


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(sightings)")}
        for name, decl in _MIGRATIONS:
            if name not in have:
                self._conn.execute(f"ALTER TABLE sightings ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self._conn.close()

    # --- writes -----------------------------------------------------------
    def add_visit(
        self,
        species: str,
        confidence: float,
        image_path: str | None = None,
        detector_conf: float | None = None,
        first_ts: datetime | None = None,
        last_ts: datetime | None = None,
        frames: int = 1,
    ) -> int:
        first_ts = first_ts or datetime.now()
        last_ts = last_ts or first_ts
        cur = self._conn.execute(
            "INSERT INTO sightings (ts, species, confidence, image_path, detector_conf,"
            " last_ts, frames) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                first_ts.isoformat(timespec="seconds"),
                species,
                confidence,
                image_path,
                detector_conf,
                last_ts.isoformat(timespec="seconds"),
                frames,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # --- reads for the UI -------------------------------------------------
    def week_grid(self, week_start: date) -> dict:
        """Build the weekly grid payload (Sun..Sat). Each visit counts once."""
        week_end = week_start + timedelta(days=7)
        rows = self._conn.execute(
            "SELECT ts, species, image_path, confidence FROM sightings"
            " WHERE ts >= ? AND ts < ? ORDER BY ts ASC",
            (week_start.isoformat(), week_end.isoformat()),
        ).fetchall()

        agg: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            ts = datetime.fromisoformat(r["ts"])
            day_idx = (ts.date() - week_start).days
            if 0 <= day_idx < 7:
                t = ts.strftime("%I:%M %p").lstrip("0")  # %-I is non-portable (Windows)
                agg[r["species"]][day_idx].append(
                    (t, r["image_path"], r["confidence"], ts)
                )

        species_payload = []
        for name, by_day in agg.items():
            counts = [len(by_day.get(i, [])) for i in range(7)]
            times = [[t[0] for t in by_day.get(i, [])] for i in range(7)]
            best = max(
                (item for day in by_day.values() for item in day),
                key=lambda x: x[2],
                default=(None, None, 0, None),
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
    return d - timedelta(days=(d.weekday() + 1) % 7)
