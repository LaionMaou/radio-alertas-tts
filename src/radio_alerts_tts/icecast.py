"""Transporte alternativo: fuente Icecast contra el puerto DJ de AzuraCast."""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from . import audio
from .config import Config
from .errors import TransportError

log = logging.getLogger(__name__)

_URL_CREDENTIALS = re.compile(r"icecast://[^\s@]+@")


def redact_secrets(message: str, password: str) -> str:
    """ffmpeg puede repetir la URL con credenciales en stderr."""
    message = _URL_CREDENTIALS.sub("icecast://***@", message)
    if password:
        message = message.replace(password, "***")
        encoded = quote(password, safe="")
        if encoded != password:
            message = message.replace(encoded, "***")
    return message


def _gain_args(config: Config) -> list[str]:
    flt = audio.gain_filter(config.audio_gain_db)
    return ["-af", flt] if flt else []


def _codec_args(config: Config) -> list[str]:
    if config.icecast_codec == "mp3":
        return [
            "-c:a", "libmp3lame",
            "-b:a", config.audio_bitrate,
            "-f", "mp3",
            "-content_type", "audio/mpeg",
        ]
    return [
        "-c:a", "libopus",
        "-b:a", config.audio_bitrate,
        "-application", "audio",
        "-f", "ogg",
        "-content_type", "application/ogg",
    ]


def build_args(wav_path: Path, config: Config, url: str) -> list[str]:
    args = [
        audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-re",
        "-i", str(wav_path),
        "-vn", "-ac", "1", "-ar", "48000",
    ]
    args += _gain_args(config)
    args += _codec_args(config)
    args += ["-flush_packets", "1", url]
    return args


def build_loop_args(
    list_path: Path,
    config: Config,
    url: str,
) -> list[str]:
    """Arranque una vez y cuerpo en bucle, en una sola conexión al puerto DJ."""
    args = [
        audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-re", "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-vn", "-ac", "1", "-ar", "48000",
    ]
    args += _gain_args(config)
    args += _codec_args(config)
    args += ["-flush_packets", "1", url]
    return args


def stream(
    wav_path: Path,
    config: Config,
    *,
    timeout_seconds: float,
    should_stop: Callable[[], bool] | None = None,
    loop_list: Path | None = None,
) -> None:
    url = config.icecast_url
    if not url:
        raise TransportError("Icecast: falta AZURACAST_DJ_HOST / AZURACAST_DJ_PORT")

    if loop_list is not None:
        args = build_loop_args(loop_list, config, url)
    else:
        args = build_args(wav_path, config, url)

    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    deadline = time.monotonic() + max(30.0, timeout_seconds) if timeout_seconds else None
    while True:
        try:
            _, stderr = process.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if should_stop is not None and should_stop():
                process.kill()
                process.communicate()
                return
            if deadline is not None and time.monotonic() > deadline:
                process.kill()
                process.communicate()
                raise TransportError("Icecast: tiempo de transmisión agotado")

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
        joined = " | ".join(detail[-5:]) or "sin detalle"
        raise TransportError(
            "Icecast falló: " + redact_secrets(joined, config.streamer_password)
        )
