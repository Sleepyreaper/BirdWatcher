"""Config loading: YAML merge, nested overlay, and env overrides."""
from __future__ import annotations

from birdwatcher.config import Config, load_config


def test_defaults_present():
    c = Config()
    assert c.web.port == 8000
    assert c.pipeline.max_image_bytes > 0
    assert c.web.max_content_length > 0


def test_yaml_merge_is_nested_and_partial(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "web:\n  port: 9999\ncamera:\n  rtsp_url: rtsp://example/stream\n",
        encoding="utf-8",
    )
    c = load_config(cfg_file)
    assert c.web.port == 9999                       # overridden
    assert c.web.host == "127.0.0.1"                # untouched default preserved
    assert c.camera.rtsp_url == "rtsp://example/stream"


def test_unknown_keys_are_ignored(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("web:\n  bogus_key: 1\n", encoding="utf-8")
    c = load_config(cfg_file)          # must not raise
    assert not hasattr(c.web, "bogus_key")


def test_env_override_rtsp(tmp_path, monkeypatch):
    monkeypatch.setenv("RTSP_URL", "rtsp://from-env/cam")
    c = load_config(tmp_path / "does_not_exist.yaml")
    assert c.camera.rtsp_url == "rtsp://from-env/cam"
