"""Carga y validación de configuración desde variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

from .errors import ConfigError

VALID_KINDS = ("eew", "tsunami", "typhoon", "other")
VALID_TRANSPORTS = ("webdj", "icecast")
VALID_CODECS = ("opus", "mp3")
VALID_VOICES = ("jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo")

MAX_TEXT_HARD_LIMIT = 600
MAX_LEAD_IN_SECONDS = 15


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    value = env.get(key)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _get_secret(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    return value if value is not None else ""


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _get(env, key, "").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "si", "sí", "on")


def _get_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = _get(env, key, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} debe ser un número entero") from exc


def _get_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _get(env, key, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} debe ser un número") from exc


def build_ws_url(base_url: str, short_name: str, dj_mount: str) -> str:
    """Construye la URL del WebDJ de AzuraCast.

    AzuraCast la arma como ``{base}{/webdj/<short_name>}{dj_mount_point}`` y
    siempre la sirve por el puerto DJ (ver docs/PROTOCOLO_WEBDJ.md).
    """
    if not base_url or not short_name:
        return ""

    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ConfigError("AZURACAST_BASE_URL debe empezar con http:// o https://")

    scheme = "wss" if parts.scheme == "https" else "ws"
    base_path = parts.path.rstrip("/")
    mount = "/" + dj_mount.lstrip("/") if dj_mount else "/"
    path = f"{base_path}/webdj/{quote(short_name, safe='')}{mount}"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


@dataclass(frozen=True)
class Config:
    azuracast_base_url: str = ""
    station_short_name: str = ""
    dj_mount: str = "/"
    streamer_user: str = ""
    streamer_password: str = ""
    ws_url_override: str = ""
    dj_host: str = ""
    dj_port: int = 8005
    tls_verify: bool = True

    transport: str = "webdj"
    icecast_codec: str = "opus"
    enable_icecast_fallback: bool = True
    drain_seconds: float = 1.5
    lead_in_seconds: float = 5.0
    chime_enabled: bool = True
    open_timeout: float = 10.0

    tts_voice: str = "jf_alpha"
    tts_speed: float = 1.0
    audio_bitrate: str = "32k"
    audio_gain_db: float = 0.0

    dry_run: bool = False
    notify_in_dry_run: bool = False
    discord_webhook_url: str = ""

    api_token: str = ""
    api_host: str = "0.0.0.0"
    api_port: int = 8090

    log_level: str = "INFO"
    data_dir: Path = Path("data")
    latest_wins: bool = True
    dedupe_window_seconds: int = 60
    max_text_chars: int = 300
    artifact_retention_days: int = 14
    metadata_artist: str = "Kuma Radio"

    @property
    def ws_url(self) -> str:
        if self.ws_url_override:
            return self.ws_url_override
        return build_ws_url(self.azuracast_base_url, self.station_short_name, self.dj_mount)

    @property
    def icecast_url(self) -> str:
        if not self.dj_host or not self.dj_port:
            return ""
        mount = "/" + self.dj_mount.lstrip("/") if self.dj_mount else "/"
        user = quote(self.streamer_user, safe="")
        password = quote(self.streamer_password, safe="")
        return f"icecast://{user}:{password}@{self.dj_host}:{self.dj_port}{mount}"

    def safe_summary(self) -> dict[str, Any]:
        """Configuración sin secretos, apta para logs y metadatos."""
        return {
            "azuracast_base_url": self.azuracast_base_url,
            "station_short_name": self.station_short_name,
            "dj_mount": self.dj_mount,
            "ws_url": self.ws_url,
            "dj_host": self.dj_host,
            "dj_port": self.dj_port,
            "transport": self.transport,
            "icecast_codec": self.icecast_codec,
            "enable_icecast_fallback": self.enable_icecast_fallback,
            "lead_in_seconds": self.lead_in_seconds,
            "chime_enabled": self.chime_enabled,
            "tts_voice": self.tts_voice,
            "tts_speed": self.tts_speed,
            "audio_bitrate": self.audio_bitrate,
            "audio_gain_db": self.audio_gain_db,
            "dry_run": self.dry_run,
            "latest_wins": self.latest_wins,
            "dedupe_window_seconds": self.dedupe_window_seconds,
            "max_text_chars": self.max_text_chars,
            "streamer_credentials": bool(self.streamer_user and self.streamer_password),
            "discord_configured": bool(self.discord_webhook_url),
            "api_token_configured": bool(self.api_token),
        }


def load_config(env: Mapping[str, str] | None = None) -> Config:
    source = os.environ if env is None else env

    config = Config(
        azuracast_base_url=_get(source, "AZURACAST_BASE_URL").rstrip("/"),
        station_short_name=_get(source, "AZURACAST_STATION_SHORT_NAME"),
        dj_mount=_get(source, "AZURACAST_DJ_MOUNT", "/"),
        streamer_user=_get(source, "AZURACAST_STREAMER_USER"),
        streamer_password=_get_secret(source, "AZURACAST_STREAMER_PASSWORD"),
        ws_url_override=_get(source, "AZURACAST_WS_URL"),
        dj_host=_get(source, "AZURACAST_DJ_HOST"),
        dj_port=_get_int(source, "AZURACAST_DJ_PORT", 8005),
        tls_verify=_get_bool(source, "AZURACAST_TLS_VERIFY", True),
        transport=_get(source, "TRANSPORT", "webdj").lower(),
        icecast_codec=_get(source, "ICECAST_CODEC", "opus").lower(),
        enable_icecast_fallback=_get_bool(source, "ENABLE_ICECAST_FALLBACK", True),
        drain_seconds=_get_float(source, "DRAIN_SECONDS", 1.5),
        lead_in_seconds=_get_float(source, "LEAD_IN_SECONDS", 5.0),
        chime_enabled=_get_bool(source, "CHIME_ENABLED", True),
        open_timeout=_get_float(source, "OPEN_TIMEOUT_SECONDS", 10.0),
        tts_voice=_get(source, "TTS_VOICE", "jf_alpha"),
        tts_speed=_get_float(source, "TTS_SPEED", 1.0),
        audio_bitrate=_get(source, "AUDIO_BITRATE", "32k"),
        audio_gain_db=_get_float(source, "AUDIO_GAIN_DB", 0.0),
        dry_run=_get_bool(source, "DRY_RUN", False),
        notify_in_dry_run=_get_bool(source, "NOTIFY_IN_DRY_RUN", False),
        discord_webhook_url=_get_secret(source, "DISCORD_WEBHOOK_URL"),
        api_token=_get_secret(source, "API_TOKEN"),
        api_host=_get(source, "API_HOST", "0.0.0.0"),
        api_port=_get_int(source, "API_PORT", 8090),
        log_level=_get(source, "LOG_LEVEL", "INFO").upper(),
        data_dir=Path(_get(source, "DATA_DIR", "data")),
        latest_wins=_get_bool(source, "LATEST_WINS", True),
        dedupe_window_seconds=_get_int(source, "DEDUPE_WINDOW_SECONDS", 60),
        max_text_chars=_get_int(source, "MAX_TEXT_CHARS", 300),
        artifact_retention_days=_get_int(source, "ARTIFACT_RETENTION_DAYS", 14),
        metadata_artist=_get(source, "METADATA_ARTIST", "Kuma Radio"),
    )

    validate_config(config)
    return config


def validate_config(config: Config) -> None:
    if config.transport not in VALID_TRANSPORTS:
        raise ConfigError(f"TRANSPORT debe ser uno de {VALID_TRANSPORTS}")
    if config.icecast_codec not in VALID_CODECS:
        raise ConfigError(f"ICECAST_CODEC debe ser uno de {VALID_CODECS}")
    if config.tts_voice not in VALID_VOICES:
        raise ConfigError(f"TTS_VOICE debe ser uno de {VALID_VOICES}")
    if config.tts_speed <= 0:
        raise ConfigError("TTS_SPEED debe ser mayor que cero")
    if not 1 <= config.dj_port <= 65535:
        raise ConfigError("AZURACAST_DJ_PORT debe estar entre 1 y 65535")
    if not 1 <= config.api_port <= 65535:
        raise ConfigError("API_PORT debe estar entre 1 y 65535")
    if not 1 <= config.max_text_chars <= MAX_TEXT_HARD_LIMIT:
        raise ConfigError(f"MAX_TEXT_CHARS debe estar entre 1 y {MAX_TEXT_HARD_LIMIT}")
    if config.drain_seconds < 0:
        raise ConfigError("DRAIN_SECONDS no puede ser negativo")
    if not 0 <= config.lead_in_seconds <= MAX_LEAD_IN_SECONDS:
        raise ConfigError(f"LEAD_IN_SECONDS debe estar entre 0 y {MAX_LEAD_IN_SECONDS}")

    if config.ws_url_override and not config.ws_url_override.startswith(("ws://", "wss://")):
        raise ConfigError("AZURACAST_WS_URL debe empezar con ws:// o wss://")

    if config.dry_run:
        return

    if not config.streamer_user or not config.streamer_password:
        raise ConfigError(
            "Faltan AZURACAST_STREAMER_USER / AZURACAST_STREAMER_PASSWORD "
            "(usá DRY_RUN=true para probar sin credenciales)"
        )

    if config.transport == "webdj" and not config.ws_url:
        raise ConfigError(
            "Para TRANSPORT=webdj hacen falta AZURACAST_BASE_URL y "
            "AZURACAST_STATION_SHORT_NAME, o bien AZURACAST_WS_URL"
        )

    if config.transport == "icecast" and not config.icecast_url:
        raise ConfigError("Para TRANSPORT=icecast hacen falta AZURACAST_DJ_HOST y AZURACAST_DJ_PORT")

    if config.enable_icecast_fallback and not config.icecast_url:
        raise ConfigError(
            "ENABLE_ICECAST_FALLBACK=true requiere AZURACAST_DJ_HOST y AZURACAST_DJ_PORT"
        )
