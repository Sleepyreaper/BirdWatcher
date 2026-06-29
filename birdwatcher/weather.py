"""Hourly weather for the day-drill view, via Open-Meteo (free, no API key).

We only need a per-hour icon + temperature to sit above the hourly bird grid,
like BirdNET-Go. Results are cached in-memory per (date, lat, lon) so paging
around doesn't hammer the API; any failure yields [] and the row simply hides.
"""

from __future__ import annotations

import json
import time as _time
import urllib.request
from datetime import date

# WMO weather codes -> (emoji, short label). Grouped to a handful of icons.
def _icon(code: int | None) -> tuple[str, str]:
    if code is None:
        return ("", "")
    c = int(code)
    if c == 0:
        return ("☀️", "clear")            # ☀️
    if c == 1:
        return ("\U0001F324️", "mainly clear")  # 🌤️
    if c == 2:
        return ("⛅", "partly cloudy")           # ⛅
    if c == 3:
        return ("☁️", "overcast")          # ☁️
    if c in (45, 48):
        return ("\U0001F32B️", "fog")           # 🌫️
    if c in (51, 53, 55, 56, 57):
        return ("\U0001F326️", "drizzle")       # 🌦️
    if c in (61, 63, 65, 66, 67):
        return ("\U0001F327️", "rain")          # 🌧️
    if c in (71, 73, 75, 77, 85, 86):
        return ("\U0001F328️", "snow")          # 🌨️
    if c in (80, 81, 82):
        return ("\U0001F326️", "showers")       # 🌦️
    if c in (95, 96, 99):
        return ("⛈️", "thunderstorm")      # ⛈️
    return ("", "")


_cache: dict[tuple, tuple[float, list]] = {}


def _normalize_hourly(data: dict) -> list[dict]:
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    codes = hourly.get("weather_code") or []
    out: list[dict] = []
    for i, t in enumerate(times):
        if not isinstance(t, str) or len(t) < 13:
            continue
        try:
            hour = int(t[11:13])
        except ValueError:
            continue
        code = codes[i] if i < len(codes) else None
        emoji, label = _icon(code)
        out.append({
            "hour": hour,
            "temp": temps[i] if i < len(temps) else None,
            "code": code,
            "icon": emoji,
            "label": label,
        })
    return out


def hourly_weather(day: date, lat: float, lon: float, ttl: float = 1800.0) -> list[dict]:
    """Return [{hour, temp, code, icon, label}] (24 rows) for `day`, or [].

    If a cached value exists but is stale, we attempt a refresh; on refresh failure,
    the stale cached value is returned rather than dropping the weather row entirely.
    """
    key = (day.isoformat(), round(lat, 3), round(lon, 3))
    now = _time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=temperature_2m,weather_code"
        f"&start_date={day.isoformat()}&end_date={day.isoformat()}"
        "&temperature_unit=fahrenheit&timezone=auto"
    )
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.load(resp)
        out = _normalize_hourly(data)
        _cache[key] = (now, out)
        return out
    except Exception:
        if hit:
            return hit[1]
        _cache[key] = (now, [])
        return []
