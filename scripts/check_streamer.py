#!/usr/bin/env python3
"""Fase 2: prueba la cuenta Streamer/DJ contra AzuraCast con un tono corto."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from radio_alerts_tts import audio, icecast, webdj  # noqa: E402
from radio_alerts_tts.config import (  # noqa: E402
    VALID_TRANSPORTS,
    load_config,
    validate_config,
)
from radio_alerts_tts.errors import AudioError, ConfigError, TransportError  # noqa: E402
from radio_alerts_tts.logging_setup import force_utf8_stdio  # noqa: E402


def make_tone(path: Path, seconds: float, frequency: int) -> None:
    args = [
        audio.FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration={seconds}:sample_rate=48000",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-y",
        str(path),
    ]
    subprocess.run(args, check=True, timeout=30)


def main() -> int:
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--frequency", type=int, default=440)
    parser.add_argument("--transport", choices=VALID_TRANSPORTS)
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"ERROR de configuración: {exc}")
        return 2

    if args.transport:
        config = replace(config, transport=args.transport)
        try:
            validate_config(config)
        except ConfigError as exc:
            print(f"ERROR de configuración: {exc}")
            return 2

    if not config.streamer_user or not config.streamer_password:
        print(
            "ERROR: hacen falta AZURACAST_STREAMER_USER y "
            "AZURACAST_STREAMER_PASSWORD para probar el streamer."
        )
        return 2

    print(f"Transporte: {config.transport}")
    if config.transport == "webdj":
        print(f"WebDJ: {config.ws_url}")
    else:
        mount = "/" + config.dj_mount.lstrip("/")
        print(f"Icecast: {config.dj_host}:{config.dj_port}{mount}")
    print(
        f"Emitiendo un tono de {args.seconds:.1f}s a {args.frequency} Hz "
        f"tras {config.lead_in_seconds:.1f}s de silencio (LEAD_IN_SECONDS)"
        f"{' y el timbre de 4 pings' if config.chime_enabled else ''}. "
        "Se va a escuchar en la radio."
    )

    try:
        with TemporaryDirectory() as tmp:
            tone_path = Path(tmp) / "tone.wav"
            make_tone(tone_path, args.seconds, args.frequency)
            stream_path = audio.add_lead_in(
                tone_path,
                config.lead_in_seconds,
                Path(tmp) / "tone-lead.wav",
                chime=config.chime_enabled,
            )

            if config.transport == "webdj":
                webdj.stream(
                    config,
                    audio.iter_webm_chunks(
                        stream_path,
                        bitrate=config.audio_bitrate,
                        gain_db=config.audio_gain_db,
                    ),
                    title="接続テスト",
                    artist=config.metadata_artist,
                )
            else:
                icecast.stream(
                    stream_path,
                    config,
                    timeout_seconds=args.seconds + config.lead_in_seconds + 60,
                )
    except TransportError as exc:
        print(f"ERROR de transporte: {exc}")
        return 1
    except (AudioError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR de audio: {exc}")
        return 1

    print("OK: conexión y transmisión completadas. El AutoDJ debería haber retomado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
