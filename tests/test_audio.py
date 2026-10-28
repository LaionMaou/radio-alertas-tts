from __future__ import annotations

from pathlib import Path

from radio_alerts_tts.audio import build_webm_args, gain_filter

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
