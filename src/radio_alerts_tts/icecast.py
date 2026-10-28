"""Transporte alternativo: fuente Icecast contra el puerto DJ de AzuraCast."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
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


def build_args(wav_path: Path, config: Config, url: str) -> list[str]:
    args = [
        audio.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-re",
        "-i", str(wav_path),
        "-vn", "-ac", "1", "-ar", "48000",
    ]

    flt = audio.gain_filter(config.audio_gain_db)
    if flt:
        args += ["-af", flt]

    if config.icecast_codec == "mp3":
        args += [
            "-c:a", "libmp3lame",
            "-b:a", config.audio_bitrate,
            "-f", "mp3",
            "-content_type", "audio/mpeg",
        ]
    else:
        args += [
            "-c:a", "libopus",
            "-b:a", config.audio_bitrate,
            "-application", "audio",
            "-f", "ogg",
            "-content_type", "application/ogg",
        ]

    args += ["-flush_packets", "1", url]
    return args


def stream(wav_path: Path, config: Config, *, timeout_seconds: float) -> None:
    url = config.icecast_url
    if not url:
        raise TransportError("Icecast: falta AZURACAST_DJ_HOST / AZURACAST_DJ_PORT")

    process = subprocess.Popen(
        build_args(wav_path, config, url),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    try:
        _, stderr = process.communicate(timeout=max(30.0, timeout_seconds))
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise TransportError("Icecast: tiempo de transmisión agotado") from exc

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
        joined = " | ".join(detail[-5:]) or "sin detalle"
        raise TransportError(
            "Icecast falló: " + redact_secrets(joined, config.streamer_password)
        )
