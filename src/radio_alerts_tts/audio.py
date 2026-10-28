"""Codificación con ffmpeg: WAV -> WebM/Opus, en tiempo real o a archivo."""

from __future__ import annotations

import collections
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterator

from .errors import AudioError

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
CHUNK_SIZE = 4096


def gain_filter(gain_db: float) -> str | None:
    if abs(gain_db) < 0.01:
        return None
    return f"volume={gain_db}dB,alimiter=limit=0.95"


BED_FREQUENCY_HZ = 440
BED_LEVEL = 0.12
BED_PING_INTERVAL = 1.0

URGENT_FREQUENCIES_HZ = (880, 1175)
URGENT_LEVELS = (0.4, 0.25)
URGENT_PING_INTERVAL = 0.3
URGENT_DECAY = 9


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


def urgent_source(seconds: float) -> str:
    """Repique estridente para 震度7 o más: dos tonos disonantes y seco."""
    wave = "+".join(
        f"{level}*sin(2*PI*{frequency}*t)"
        for frequency, level in zip(URGENT_FREQUENCIES_HZ, URGENT_LEVELS)
    )
    return (
        f"aevalsrc=({wave})*exp(-{URGENT_DECAY}*mod(t\\,{URGENT_PING_INTERVAL}))"
        f":s=48000:d={seconds:g}"
    )


def lead_in_source(seconds: float, tone: str) -> str:
    return urgent_source(seconds) if tone == "urgent" else bed_source(seconds)


def _mono_input(index: int) -> str:
    return f"[{index}:a]aformat=sample_rates=48000:channel_layouts=mono[a{index}];"


def build_pad_args(
    wav_path: Path,
    seconds: float,
    output: Path,
    *,
    tone: str = "soft",
    sound: bool = True,
) -> list[str]:
    head = lead_in_source(seconds, tone) if sound else "anullsrc=r=48000:cl=mono"
    args = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-t", f"{seconds:g}", "-i", head,
        "-i", str(wav_path),
        "-filter_complex", _mono_input(0) + _mono_input(1)
        + "[a0][a1]concat=n=2:v=0:a=1[a]",
        "-map", "[a]",
        "-c:a", "pcm_s16le", "-ac", "1", "-ar", "48000",
        str(output),
    ]
    return args


def add_lead_in(
    wav_path: Path,
    seconds: float,
    output: Path,
    *,
    tone: str = "soft",
    sound: bool = True,
) -> Path:
    """Devuelve un WAV con el arranque delante del anuncio.

    Liquidsoap tarda 4-5 s en conmutar al streamer y lo ya bufferizado puede
    descartarse o llegar a sonar; en ninguno de los dos casos queremos aire
    muerto, así que el arranque va lleno: cama suave para lo moderado y
    repique estridente para 震度7 o más.

    Tiene que ser un archivo real: con `-re`, ffmpeg adelanta el audio que
    genera un filtro (adelay, concat sobre anullsrc) en lugar de transmitirlo a
    ritmo real, y el anuncio vuelve a caer en la ventana descartada.
    """
    if seconds <= 0:
        return wav_path

    try:
        result = subprocess.run(
            build_pad_args(wav_path, seconds, output, tone=tone, sound=sound),
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


def _webm_output_args(bitrate: str, output: str) -> list[str]:
    return [
        "-c:a", "libopus",
        "-b:a", bitrate,
        "-application", "audio",
        "-frame_duration", "20",
        "-f", "webm",
        "-cluster_time_limit", "1000",
        "-flush_packets", "1",
        output,
    ]


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

    return args + _webm_output_args(bitrate, output)


def build_loop_wav(wav_path: Path, output: Path, gap_seconds: float) -> Path:
    """Cuerpo del bucle: el anuncio más una pausa, listo para repetirse."""
    args = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(wav_path),
        "-f", "lavfi", "-t", f"{gap_seconds:g}", "-i", "anullsrc=r=48000:cl=mono",
        "-filter_complex", _mono_input(0) + _mono_input(1)
        + "[a0][a1]concat=n=2:v=0:a=1[a]",
        "-map", "[a]",
        "-c:a", "pcm_s16le", "-ac", "1", "-ar", "48000",
        str(output),
    ]
    try:
        result = subprocess.run(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioError("ffmpeg excedió el tiempo al preparar el bucle") from exc

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise AudioError("ffmpeg falló al preparar el bucle: " + " | ".join(detail[-3:]))
    return output


def _ffconcat_path(path: Path) -> str:
    return path.as_posix().replace("'", "'\\''")


def build_loop_list(
    lead_path: Path,
    body_path: Path,
    output: Path,
    repeats: int,
) -> Path:
    """Lista del demuxer concat: el arranque una vez y el cuerpo repetido.

    El filtro ``concat`` más ``-stream_loop`` reinicia el reloj de ``-re`` en
    cada vuelta y transmite 1.3x más rápido; el demuxer concat conserva los
    timestamps y respeta el ritmo real (medido).
    """
    lines = ["ffconcat version 1.0", f"file '{_ffconcat_path(lead_path)}'"]
    lines += [f"file '{_ffconcat_path(body_path)}'"] * max(1, repeats)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def build_webm_loop_args(
    list_path: Path,
    bitrate: str,
    gain_db: float,
    output: str,
) -> list[str]:
    """Un solo contenedor: el anuncio una vez y después el cuerpo en bucle."""
    args = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-re", "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-vn", "-ac", "1", "-ar", "48000",
    ]

    flt = gain_filter(gain_db)
    if flt:
        args += ["-af", flt]

    return args + _webm_output_args(bitrate, output)


def iter_webm_chunks(
    wav_path: Path,
    bitrate: str,
    gain_db: float,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[bytes]:
    """Emite WebM/Opus a ritmo real, como lo haría el WebDJ en el navegador."""
    args = build_webm_args(wav_path, bitrate, gain_db, "pipe:1", realtime=True)
    yield from _iter_process_chunks(args, should_stop)


def iter_webm_loop_chunks(
    list_path: Path,
    bitrate: str,
    gain_db: float,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[bytes]:
    args = build_webm_loop_args(list_path, bitrate, gain_db, "pipe:1")
    yield from _iter_process_chunks(args, should_stop)


def _iter_process_chunks(
    args: list[str],
    should_stop: Callable[[], bool] | None,
) -> Iterator[bytes]:
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
            if should_stop is not None and should_stop():
                break
            chunk = process.stdout.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

        if should_stop is not None and should_stop():
            return

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
