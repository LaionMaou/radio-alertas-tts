"""Codificación con ffmpeg: WAV -> WebM/Opus, en tiempo real o a archivo."""

from __future__ import annotations

import collections
import os
import subprocess
import threading
from pathlib import Path
from typing import Iterator

from .errors import AudioError

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
CHUNK_SIZE = 4096


def gain_filter(gain_db: float) -> str | None:
    if abs(gain_db) < 0.01:
        return None
    return f"volume={gain_db}dB,alimiter=limit=0.95"


CHIME_PINGS = 4
CHIME_PING_INTERVAL = 0.5
CHIME_FREQUENCY_HZ = 880
CHIME_LEVEL = 0.45
CHIME_SECONDS = CHIME_PINGS * CHIME_PING_INTERVAL

BED_FREQUENCY_HZ = 440
BED_LEVEL = 0.12
BED_PING_INTERVAL = 1.0


def chime_source() -> str:
    """Fuente lavfi del timbre: `CHIME_PINGS` pings con caída exponencial."""
    return (
        f"aevalsrc={CHIME_LEVEL}*sin(2*PI*{CHIME_FREQUENCY_HZ}*t)"
        f"*exp(-7*mod(t\\,{CHIME_PING_INTERVAL}))"
        f":s=48000:d={CHIME_SECONDS:g}"
    )


def bed_source(seconds: float) -> str:
    """Cama suave para el arranque: un ping grave por segundo.

    Lo que se envíe antes de la conmutación puede llegar a sonar en el aire,
    así que este tramo va con timbre y no con silencio.
    """
    return (
        f"aevalsrc={BED_LEVEL}*sin(2*PI*{BED_FREQUENCY_HZ}*t)"
        f"*exp(-4*mod(t\\,{BED_PING_INTERVAL}))"
        f":s=48000:d={seconds:g}"
    )


def _mono_input(index: int) -> str:
    return f"[{index}:a]aformat=sample_rates=48000:channel_layouts=mono[a{index}];"


def build_pad_args(
    wav_path: Path,
    seconds: float,
    output: Path,
    *,
    chime: bool = False,
) -> list[str]:
    head = bed_source(seconds) if chime else "anullsrc=r=48000:cl=mono"
    args = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-t", f"{seconds:g}", "-i", head,
    ]
    if chime:
        args += ["-f", "lavfi", "-i", chime_source()]
    args += ["-i", str(wav_path)]

    speech_index = 2 if chime else 1
    graph = _mono_input(0)
    if chime:
        graph += _mono_input(1)
    graph += _mono_input(speech_index)
    labels = "".join(f"[a{i}]" for i in range(speech_index + 1))
    graph += f"{labels}concat=n={speech_index + 1}:v=0:a=1[a]"

    return args + [
        "-filter_complex", graph,
        "-map", "[a]",
        "-c:a", "pcm_s16le", "-ac", "1", "-ar", "48000",
        str(output),
    ]


def add_lead_in(
    wav_path: Path,
    seconds: float,
    output: Path,
    *,
    chime: bool = False,
) -> Path:
    """Devuelve un WAV con la cama de timbre y los pings delante del anuncio.

    Liquidsoap tarda 4-5 s en conmutar al streamer y lo ya bufferizado puede
    descartarse o llegar a sonar; en ninguno de los dos casos queremos aire
    muerto, así que el arranque va lleno de timbre. Los pings fuertes quedan al
    final, justo antes de la voz: si la conmutación se come el comienzo, lo que
    se pierde es cama, no la señal de que empieza el anuncio.

    Tiene que ser un archivo real: con `-re`, ffmpeg adelanta el audio que
    genera un filtro (adelay, concat sobre anullsrc) en lugar de transmitirlo a
    ritmo real, y el anuncio vuelve a caer en la ventana descartada.
    """
    if seconds <= 0:
        return wav_path

    try:
        result = subprocess.run(
            build_pad_args(wav_path, seconds, output, chime=chime),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioError("ffmpeg excedió el tiempo al preparar el arranque") from exc

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise AudioError("ffmpeg falló al preparar el arranque: " + " | ".join(detail[-3:]))
    return output


def build_webm_args(
    wav_path: Path,
    bitrate: str,
    gain_db: float,
    output: str,
    *,
    realtime: bool,
) -> list[str]:
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if realtime:
        args.append("-re")
    args += ["-i", str(wav_path), "-vn", "-ac", "1", "-ar", "48000"]

    flt = gain_filter(gain_db)
    if flt:
        args += ["-af", flt]

    args += [
        "-c:a", "libopus",
        "-b:a", bitrate,
        "-application", "audio",
        "-frame_duration", "20",
        "-f", "webm",
        "-cluster_time_limit", "1000",
        "-flush_packets", "1",
        output,
    ]
    return args


def iter_webm_chunks(wav_path: Path, bitrate: str, gain_db: float) -> Iterator[bytes]:
    """Emite WebM/Opus a ritmo real, como lo haría el WebDJ en el navegador."""
    args = build_webm_args(wav_path, bitrate, gain_db, "pipe:1", realtime=True)
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    stderr_tail: collections.deque[str] = collections.deque(maxlen=40)

    def _drain_stderr() -> None:
        assert process.stderr is not None
        for line in iter(process.stderr.readline, b""):
            stderr_tail.append(line.decode("utf-8", errors="replace").rstrip())

    reader = threading.Thread(target=_drain_stderr, daemon=True)
    reader.start()

    try:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

        return_code = process.wait()
        if return_code != 0:
            detail = " | ".join(stderr_tail) or "sin detalle"
            raise AudioError(f"ffmpeg falló al codificar WebM ({return_code}): {detail}")
    finally:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def encode_webm_file(
    wav_path: Path,
    output_path: Path,
    bitrate: str,
    gain_db: float,
    duration_seconds: float,
) -> None:
    args = build_webm_args(wav_path, bitrate, gain_db, str(output_path), realtime=False)
    timeout = max(30.0, duration_seconds * 3 + 30.0)
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioError("ffmpeg excedió el tiempo al generar el WebM") from exc

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise AudioError("ffmpeg falló al generar el WebM: " + " | ".join(detail[-5:]))


def probe_duration(path: Path) -> float:
    args = [
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode != 0:
        raise AudioError("ffprobe no pudo leer el archivo")
    try:
        return float(result.stdout.decode().strip())
    except ValueError as exc:
        raise AudioError("ffprobe devolvió una duración inválida") from exc
