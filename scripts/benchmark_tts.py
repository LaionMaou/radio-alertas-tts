#!/usr/bin/env python3
"""Fase 1: mide tiempo de generación, RTF y memoria de Kokoro en este equipo."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from radio_alerts_tts import tts  # noqa: E402
from radio_alerts_tts.logging_setup import force_utf8_stdio  # noqa: E402

try:
    import resource
except ImportError:  # Windows
    resource = None

DEFAULT_TEXT = (
    "緊急地震速報。宮城県沖で強い地震が発生しました。"
    "震度5強を観測しました。強い揺れに警戒してください。"
)


def peak_rss_mb() -> float | None:
    if resource is None:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(usage / divisor, 1)


def main() -> int:
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default=DEFAULT_TEXT, help="texto a sintetizar")
    parser.add_argument("--text-file", type=Path, help="leer el texto de un archivo")
    parser.add_argument("--voice", default="jf_alpha")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--out", type=Path, help="guardar el resumen como JSON")
    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs debe ser mayor que cero")

    text = (
        args.text_file.read_text(encoding="utf-8").strip()
        if args.text_file
        else args.text
    )
    if not text:
        parser.error("el texto está vacío")

    print(f"Voz: {args.voice} | velocidad: {args.speed}")
    print(f"Texto: {len(text)} caracteres")
    print("Cargando el modelo (primera vez incluye descarga/ensamblado)…")

    load_seconds = tts.warmup(args.voice)
    print(f"Modelo listo en {load_seconds:.2f} s\n")

    rows: list[dict[str, float]] = []
    with TemporaryDirectory() as tmp:
        for index in range(args.runs):
            wav_path = Path(tmp) / f"bench-{index}.wav"
            cpu_started = time.process_time()
            started = time.perf_counter()
            audio_seconds = tts.synthesize_to_wav(
                text, wav_path, voice=args.voice, speed=args.speed
            )
            generation_seconds = time.perf_counter() - started
            cpu_seconds = time.process_time() - cpu_started
            rows.append(
                {
                    "run": index + 1,
                    "audio_seconds": round(audio_seconds, 2),
                    "generation_seconds": round(generation_seconds, 3),
                    "rtf": round(generation_seconds / audio_seconds, 3)
                    if audio_seconds
                    else 0.0,
                    "cpu_seconds": round(cpu_seconds, 3),
                }
            )
            print(
                f"  #{index + 1}: audio {audio_seconds:.2f}s | "
                f"generación {generation_seconds:.2f}s | "
                f"RTF {rows[-1]['rtf']:.2f} | CPU {cpu_seconds:.2f}s"
            )

    rss = peak_rss_mb()
    summary = {
        "voice": args.voice,
        "speed": args.speed,
        "text_chars": len(text),
        "runs": args.runs,
        "load_seconds": round(load_seconds, 2),
        "median_generation_seconds": round(
            statistics.median(r["generation_seconds"] for r in rows), 3
        ),
        "median_audio_seconds": round(
            statistics.median(r["audio_seconds"] for r in rows), 2
        ),
        "median_rtf": round(statistics.median(r["rtf"] for r in rows), 3),
        "peak_rss_mb": rss,
        "measurements": rows,
    }

    print("\n| # | texto (s) | audio (s) | generación (s) | RTF | CPU (s) |")
    print("|---|-----------|-----------|----------------|-----|---------|")
    for row in rows:
        print(
            f"| {row['run']} | {len(text)} | {row['audio_seconds']:.2f} | "
            f"{row['generation_seconds']:.2f} | {row['rtf']:.2f} | "
            f"{row['cpu_seconds']:.2f} |"
        )

    print(
        f"\nResumen: RTF mediano {summary['median_rtf']:.2f} | "
        f"generación mediana {summary['median_generation_seconds']:.2f}s | "
        f"memoria pico {rss if rss is not None else 'no disponible'} MB"
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Resumen guardado en {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
