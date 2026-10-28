from __future__ import annotations

import json

import pytest

from radio_alerts_tts.config import build_ws_url, load_config
from radio_alerts_tts.errors import ConfigError

LIVE_ENV = {
    "AZURACAST_BASE_URL": "https://radio.example.com",
    "AZURACAST_STATION_SHORT_NAME": "kuma",
    "AZURACAST_STREAMER_USER": "alert-bot",
    "AZURACAST_STREAMER_PASSWORD": "s3cret",
    "AZURACAST_DJ_HOST": "dj.internal",
    "AZURACAST_DJ_PORT": "8005",
    "API_TOKEN": "tok_abc123",
}


def test_build_ws_url_root_mount() -> None:
    assert (
        build_ws_url("https://radio.kumakuma.fm/", "kuma", "/")
        == "wss://radio.kumakuma.fm/webdj/kuma/"
    )


def test_build_ws_url_subpath_and_named_mount() -> None:
    assert (
        build_ws_url("https://host.example/radio", "kuma", "live")
        == "wss://host.example/radio/webdj/kuma/live"
    )


def test_build_ws_url_http_is_ws() -> None:
    assert (
        build_ws_url("http://127.0.0.1:8080", "k", "")
        == "ws://127.0.0.1:8080/webdj/k/"
    )


def test_build_ws_url_requires_http_scheme() -> None:
    with pytest.raises(ConfigError):
        build_ws_url("ftp://radio.example.com", "kuma", "/")


def test_build_ws_url_empty_without_inputs() -> None:
    assert build_ws_url("", "kuma", "/") == ""


def test_live_config_builds_urls(tmp_path) -> None:
    config = load_config({**LIVE_ENV, "DATA_DIR": str(tmp_path)})

    assert config.ws_url == "wss://radio.example.com/webdj/kuma/"
    assert config.icecast_url == "icecast://alert-bot:s3cret@dj.internal:8005/"
    assert config.dry_run is False


def test_icecast_url_encodes_credentials(tmp_path) -> None:
    env = {
        **LIVE_ENV,
        "AZURACAST_STREAMER_USER": "alert bot",
        "AZURACAST_STREAMER_PASSWORD": "p@ss:w/rd",
        "DATA_DIR": str(tmp_path),
    }
    config = load_config(env)

    assert config.icecast_url == (
        "icecast://alert%20bot:p%40ss%3Aw%2Frd@dj.internal:8005/"
    )


def test_ws_url_override(tmp_path) -> None:
    env = {
        "DRY_RUN": "true",
        "AZURACAST_WS_URL": "ws://127.0.0.1:8005/webdj/kuma/",
        "DATA_DIR": str(tmp_path),
    }
    config = load_config(env)

    assert config.ws_url == "ws://127.0.0.1:8005/webdj/kuma/"


def test_ws_override_must_be_websocket(tmp_path) -> None:
    env = {
        "DRY_RUN": "true",
        "AZURACAST_WS_URL": "https://radio.example.com/webdj/kuma/",
        "DATA_DIR": str(tmp_path),
    }
    with pytest.raises(ConfigError):
        load_config(env)


def test_dry_run_allows_missing_credentials(tmp_path) -> None:
    config = load_config({"DRY_RUN": "true", "DATA_DIR": str(tmp_path)})

    assert config.dry_run is True
    assert config.streamer_user == ""


def test_live_requires_streamer_credentials(tmp_path) -> None:
    env = {
        key: value
        for key, value in LIVE_ENV.items()
        if key not in ("AZURACAST_STREAMER_USER", "AZURACAST_STREAMER_PASSWORD")
    }
    with pytest.raises(ConfigError, match="STREAMER"):
        load_config({**env, "DATA_DIR": str(tmp_path)})


def test_fallback_requires_dj_host(tmp_path) -> None:
    env = {
        key: value
        for key, value in LIVE_ENV.items()
        if key != "AZURACAST_DJ_HOST"
    }
    with pytest.raises(ConfigError, match="ICECAST"):
        load_config({**env, "DATA_DIR": str(tmp_path)})


def test_fallback_can_be_disabled(tmp_path) -> None:
    env = {
        key: value
        for key, value in LIVE_ENV.items()
        if key != "AZURACAST_DJ_HOST"
    }
    config = load_config(
        {
            **env,
            "ENABLE_ICECAST_FALLBACK": "false",
            "DATA_DIR": str(tmp_path),
        }
    )

    assert config.enable_icecast_fallback is False
    assert config.icecast_url == ""


def test_invalid_transport_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_config(
            {"DRY_RUN": "true", "TRANSPORT": "pulse", "DATA_DIR": str(tmp_path)}
        )


def test_invalid_voice_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_config(
            {"DRY_RUN": "true", "TTS_VOICE": "en_us_1", "DATA_DIR": str(tmp_path)}
        )


def test_safe_summary_hides_secrets(tmp_path) -> None:
    config = load_config({**LIVE_ENV, "DATA_DIR": str(tmp_path)})
    summary = json.dumps(config.safe_summary(), ensure_ascii=False)

    assert "s3cret" not in summary
    assert config.streamer_password not in summary
    assert config.api_token not in summary
    assert '"streamer_credentials": true' in summary
