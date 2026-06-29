from __future__ import annotations

import io
from datetime import date

from birdwatcher import weather


class _Resp(io.StringIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_weather_uses_cache(monkeypatch):
    weather._cache.clear()
    calls = {"n": 0}

    def fake_urlopen(url, timeout=0):
        calls["n"] += 1
        return _Resp('{"hourly":{"time":["2026-06-29T08:00"],"temperature_2m":[77],"weather_code":[1]}}')

    monkeypatch.setattr(weather.urllib.request, "urlopen", fake_urlopen)
    day = date(2026, 6, 29)
    a = weather.hourly_weather(day, 33.94, -84.55, ttl=999)
    b = weather.hourly_weather(day, 33.94, -84.55, ttl=999)
    assert a == b
    assert calls["n"] == 1


def test_weather_returns_stale_cache_on_refresh_failure(monkeypatch):
    weather._cache.clear()
    day = date(2026, 6, 29)
    key = (day.isoformat(), round(33.94, 3), round(-84.55, 3))
    stale = [{"hour": 8, "temp": 77, "code": 1, "icon": "", "label": ""}]
    weather._cache[key] = (0.0, stale)

    def boom(url, timeout=0):
        raise TimeoutError("timeout")

    monkeypatch.setattr(weather.urllib.request, "urlopen", boom)
    out = weather.hourly_weather(day, 33.94, -84.55, ttl=1)
    assert out == stale


def test_weather_returns_empty_on_first_failure(monkeypatch):
    weather._cache.clear()

    def boom(url, timeout=0):
        raise TimeoutError("timeout")

    monkeypatch.setattr(weather.urllib.request, "urlopen", boom)
    out = weather.hourly_weather(date(2026, 6, 29), 33.94, -84.55, ttl=1)
    assert out == []
