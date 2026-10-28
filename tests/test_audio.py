from __future__ import annotations

from pathlib import Path

from radio_alerts_tts.audio import (
    BED_LEVEL,
    CHIME_LEVEL,
    CHIME_SECONDS,
    add_lead_in,
    bed_source,
    build_pad_args,
    build_webm_args,
    chime_source,
    gain_filter,
)

WAV = Path("alert.wav")


def test_webm_args_realtime_streams_to_stdout() -> None:
    args = build_webm_args(WAV, "32k", 0.0, "pipe:1", realtime=True)

    assert "-re" in args
    assert "libopus" in args
    assert "webm" in args
    assert args[-1] == "pipe:1"
    assert "audio/webm" not in args
    assert args[args.index("-b:a") + 1] == "32k"


def test_webm_args_file_skips_realtime() -> None:
    args = build_webm_args(WAV, "32k", 0.0, "out.webm", realtime=False)

    assert "-re" not in args
    assert args[-1] == "out.webm"


def test_gain_filter_off_by_default() -> None:
    assert gain_filter(0) is None
    assert gain_filter(0.004) is None


def test_gain_filter_uses_limiter() -> None:
    assert gain_filter(3.5) == "volume=3.5dB,alimiter=limit=0.95"


def test_add_lead_in_is_a_noop_without_seconds() -> None:
    assert add_lead_in(WAV, 0.0, Path("lead.wav")) == WAV


def test_pad_args_prepend_real_silence_when_the_chime_is_off() -> None:
    args = build_pad_args(WAV, 5.0, Path("lead.wav"))
    joined = " ".join(args)

    assert args[args.index("-t") + 1] == "5"
    assert "anullsrc=r=48000:cl=mono" in args
    assert "concat=n=2:v=0:a=1" in joined
    assert args[-1] == "lead.wav"
    assert "adelay" not in joined, (
        "con -re ffmpeg adelanta el silencio de un filtro: tiene que ser un WAV real"
    )


def test_pad_args_fill_the_head_with_the_bed_and_end_with_the_chime() -> None:
    args = build_pad_args(WAV, 5.0, Path("lead.wav"), chime=True)
    graph = args[args.index("-filter_complex") + 1]
    joined = " ".join(args)

    assert bed_source(5.0) in args
    assert chime_source() in args
    assert "anullsrc" not in joined, (
        "el arranque no puede ser silencio: si la conmutación llega antes, esos "
        "segundos se transmiten como aire muerto"
    )
    assert "concat=n=3:v=0:a=1" in graph
    assert "[a0][a1][a2]concat" in graph, (
        "orden: cama (0), pings fuertes (1), anuncio (2); si los pings van "
        "primero la conmutación de Liquidsoap se los come"
    )
    assert BED_LEVEL < CHIME_LEVEL
    assert CHIME_SECONDS == 2.0
