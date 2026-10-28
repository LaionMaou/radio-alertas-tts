from __future__ import annotations

import threading
import time

import pytest

from radio_alerts_tts.config import load_config
from radio_alerts_tts.models import AlertRequest, AlertResult
from radio_alerts_tts.service import AlertService


def make_config(tmp_path, **overrides):
    env = {"DRY_RUN": "true", "DATA_DIR": str(tmp_path)}
    env.update(overrides)
    return load_config(env)


def fake_result(alert: AlertRequest) -> AlertResult:
    return AlertResult(
        id=alert.id,
        status="dry-run",
        kind=alert.kind,
        text=alert.text,
        title=alert.title,
        transport="dry-run",
        tts_seconds=0.0,
        audio_seconds=0.0,
        stream_seconds=0.0,
        total_seconds=0.0,
        sent_audio=False,
        error=None,
        started_at="",
        finished_at="",
        artifact_dir="",
        wav_path=None,
        webm_path=None,
        notify_ok=None,
    )


def wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condición no alcanzada a tiempo")


def test_latest_wins_replaces_pending_alert(tmp_path) -> None:
    gate = threading.Event()
    seen: list[str] = []

    def process(alert: AlertRequest) -> AlertResult:
        seen.append(alert.id)
        gate.wait(timeout=5)
        return fake_result(alert)

    service = AlertService(make_config(tmp_path), process_fn=process)
    service.start(warmup=False)
    try:
        first = AlertRequest(text="primera")
        second = AlertRequest(text="segunda")
        third = AlertRequest(text="tercera")

        assert service.submit(first).status == "queued"
        wait_until(lambda: len(seen) == 1)

        assert service.submit(second).status == "queued"
        outcome = service.submit(third)

        assert outcome.status == "replaced"
        assert outcome.replaced_id == second.id

        gate.set()
        wait_until(lambda: len(seen) == 2)
        assert seen == [first.id, third.id]
    finally:
        gate.set()
        service.stop()


def test_result_is_cached_for_lookup(tmp_path) -> None:
    service = AlertService(make_config(tmp_path), process_fn=fake_result)
    service.start(warmup=False)
    try:
        alert = AlertRequest(text="hola")
        assert service.submit(alert).status == "queued"

        wait_until(lambda: service.get_result(alert.id) is not None)
        result = service.get_result(alert.id)

        assert result is not None
        assert result.status == "dry-run"
        assert service.get_result("no-existe") is None
    finally:
        service.stop()


def test_busy_when_latest_wins_disabled(tmp_path) -> None:
    service = AlertService(
        make_config(tmp_path, LATEST_WINS="false"),
        process_fn=fake_result,
    )
    service.submit(AlertRequest(text="primera"))
    outcome = service.submit(AlertRequest(text="segunda"))

    assert outcome.status == "busy"


def test_duplicate_within_window_is_dropped(tmp_path) -> None:
    service = AlertService(make_config(tmp_path), process_fn=fake_result)

    first = AlertRequest(text="misma", dedupe_key="eew-1")
    assert service.submit(first).status == "queued"

    outcome = service.submit(AlertRequest(text="misma", dedupe_key="eew-1"))

    assert outcome.status == "duplicate"
    assert outcome.duplicate_of == first.id


def test_replaced_alert_forgets_dedupe_key(tmp_path) -> None:
    service = AlertService(make_config(tmp_path), process_fn=fake_result)

    with_key = AlertRequest(text="con clave", dedupe_key="eew-2")
    assert service.submit(with_key).status == "queued"
    assert service.submit(AlertRequest(text="sin clave")).status == "replaced"

    outcome = service.submit(AlertRequest(text="de nuevo", dedupe_key="eew-2"))

    assert outcome.status == "replaced"


def test_rejects_text_over_limit(tmp_path) -> None:
    service = AlertService(
        make_config(tmp_path, MAX_TEXT_CHARS="10"),
        process_fn=fake_result,
    )

    with pytest.raises(ValueError, match="máximo"):
        service.submit(AlertRequest(text="x" * 11))


def test_rejects_unknown_kind(tmp_path) -> None:
    service = AlertService(make_config(tmp_path), process_fn=fake_result)

    with pytest.raises(ValueError, match="kind"):
        service.submit(AlertRequest(text="hola", kind="meteor"))


def test_rejects_out_of_range_magnitude(tmp_path) -> None:
    service = AlertService(make_config(tmp_path), process_fn=fake_result)

    with pytest.raises(ValueError, match="magnitude"):
        service.submit(AlertRequest(text="hola", magnitude=13))
