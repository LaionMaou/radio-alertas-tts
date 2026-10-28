from __future__ import annotations

from pathlib import Path

from radio_alerts_tts.audio import (
    BED_LEVEL,
    URGENT_LEVELS,
    add_lead_in,
    bed_source,
    build_loop_list,
    build_loop_wav,
    build_pad_args,
    build_webm_args,
    build_webm_loop_args,
    gain_filter,
    lead_in_source,
    urgent_source,
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


def test_pad_args_prepend_real_silence_when_the_sound_is_off() -> None:
    args = build_pad_args(WAV, 3.0, Path("lead.wav"), sound=False)
    joined = " ".join(args)

    assert args[args.index("-t") + 1] == "3"
    assert "anullsrc=r=48000:cl=mono" in args
    assert "concat=n=2:v=0:a=1" in joined
    assert args[-1] == "lead.wav"
    assert "adelay" not in joined, (
        "con -re ffmpeg adelanta el silencio de un filtro: tiene que ser un WAV real"
    )


def test_soft_lead_is_the_low_bed() -> None:
    args = build_pad_args(WAV, 3.0, Path("lead.wav"), tone="soft", sound=True)
    joined = " ".join(args)

    assert bed_source(3.0) in args
    assert urgent_source(3.0) not in args
    assert "anullsrc" not in joined, (
        "el arranque no puede ser silencio: si la conmutación llega antes, esos "
        "segundos se transmiten como aire muerto"
    )
    assert args[args.index("-filter_complex") + 1].endswith(
        "[a0][a1]concat=n=2:v=0:a=1[a]"
    )


def test_urgent_lead_is_strident_for_seven_or_more() -> None:
    args = build_pad_args(WAV, 3.0, Path("lead.wav"), tone="urgent", sound=True)

    assert urgent_source(3.0) in args
    assert bed_source(3.0) not in args
    assert len(URGENT_LEVELS) == 2
    assert max(URGENT_LEVELS) > BED_LEVEL


def test_lead_in_source_picks_the_tone() -> None:
    assert lead_in_source(3.0, "soft") == bed_source(3.0)
    assert lead_in_source(3.0, "urgent") == urgent_source(3.0)
    assert lead_in_source(3.0, "desconocido") == bed_source(3.0)


def test_loop_wav_is_speech_plus_a_gap(tmp_path, monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_run(args, **kwargs):
        captured.append(args)

        class Result:
            returncode = 0
            stderr = b""

        return Result()

    monkeypatch.setattr("radio_alerts_tts.audio.subprocess.run", fake_run)

    output = Path("loop.wav")
    assert build_loop_wav(WAV, output, 2.0) == output

    args = captured[0]
    assert args[args.index("-t") + 1] == "2"
    assert "anullsrc=r=48000:cl=mono" in args
    assert args[args.index("-filter_complex") + 1].endswith(
        "[a0][a1]concat=n=2:v=0:a=1[a]"
    )
    assert "pcm_s16le" in args


def test_loop_list_starts_with_the_head_and_repeats_the_body(tmp_path) -> None:
    listing = build_loop_list(Path("lead.wav"), Path("loop.wav"), tmp_path / "l", 3)

    lines = listing.read_text(encoding="utf-8").splitlines()

    assert lines[0] == "ffconcat version 1.0"
    assert lines[1] == "file 'lead.wav'"
    assert lines[2:] == ["file 'loop.wav'"] * 3


def test_loop_webm_is_one_paced_container() -> None:
    args = build_webm_loop_args(Path("loop.ffconcat"), "32k", 0.0, "pipe:1")

    assert args[args.index("-f") + 1] == "concat"
    assert args[args.index("-i") + 1] == "loop.ffconcat"
    assert "-stream_loop" not in args, (
        "el filtro concat con -stream_loop reinicia -re y acelera el audio"
    )
    assert args.count("-re") == 1
    assert args[-1] == "pipe:1"
