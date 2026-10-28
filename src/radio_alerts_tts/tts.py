"""Síntesis de voz japonesa con Kokoro (modelo en memoria)."""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np

from .errors import TtsError

_lock = threading.Lock()
_ready = False


def is_ready() -> bool:
    return _ready


def _write_wav(audio: np.ndarray, sample_rate: int, path: Path) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def synthesize_to_wav(text: str, path: Path, voice: str, speed: float) -> float:
    """Genera un WAV 24 kHz mono y devuelve su duración en segundos."""
    global _ready

    text = text.strip()
    if not text:
        raise TtsError("El texto de la alerta está vacío")

    from kokoro_ja_voice import synthesize

    with _lock:
        try:
            audio, sample_rate = synthesize(text, voice=voice, speed=speed)
        except Exception as exc:
            raise TtsError(f"Kokoro falló: {type(exc).__name__}: {exc}") from exc

        if audio is None or len(audio) == 0:
            raise TtsError("Kokoro no generó audio para el texto recibido")

        _write_wav(audio, sample_rate, path)
        _ready = True
        return float(len(audio) / sample_rate)


def warmup(voice: str) -> float:
    """Carga el modelo sin emitir audio útil. Devuelve los segundos que tardó."""
    global _ready

    from kokoro_ja_voice import synthesize

    started = time.perf_counter()
    with _lock:
        synthesize("テスト。", voice=voice)
        _ready = True
    return time.perf_counter() - started
