"""Orquestación: cola, TTS, entrega en vivo y auditoría."""

from __future__ import annotations

import json
import logging
import queue
import shutil
import socket
import threading
import time
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__, audio, icecast, notifier, tts, webdj
from .config import VALID_KINDS, Config
from .errors import TransportError
from .logging_setup import log_event
from .models import KIND_TITLES, AlertRequest, AlertResult, SubmitOutcome

ProcessFn = Callable[[AlertRequest], AlertResult]


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class AlertService:
    def __init__(
        self,
        config: Config,
        logger: logging.Logger | None = None,
        process_fn: ProcessFn | None = None,
    ) -> None:
        self.config = config
        self.log = logger or logging.getLogger("radio_alerts_tts")

        self._queue: queue.Queue[AlertRequest | None] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._submit_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._dedupe: dict[str, tuple[str, float]] = {}
        self._busy = False
        self._last: AlertResult | None = None
        self._results: OrderedDict[str, AlertResult] = OrderedDict()
        self._results_limit = 200
        self._model_error: str | None = None
        self._started = time.monotonic()
        self._process_fn: ProcessFn = process_fn or self._process

    def start(self, *, warmup: bool = True) -> None:
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        (self.config.data_dir / "alerts").mkdir(parents=True, exist_ok=True)

        self._thread = threading.Thread(
            target=self._worker, name="alert-worker", daemon=True
        )
        self._thread.start()

        if warmup:
            threading.Thread(
                target=self._warmup, name="tts-warmup", daemon=True
            ).start()
        if self.config.artifact_retention_days > 0:
            threading.Thread(
                target=self._cleanup_artifacts, name="artifact-cleanup", daemon=True
            ).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass

        if self._thread is not None:
            self._thread.join(timeout=5)

    def submit(self, alert: AlertRequest) -> SubmitOutcome:
        self._validate(alert)

        with self._submit_lock:
            duplicate = self._find_duplicate(alert)
            if duplicate:
                log_event(
                    self.log,
                    logging.INFO,
                    "alert.duplicate",
                    "Alerta duplicada ignorada",
                    alert_id=alert.id,
                    duplicate_of=duplicate,
                    dedupe_key=alert.dedupe_key,
                )
                return SubmitOutcome("duplicate", alert.id, duplicate_of=duplicate)

            replaced: str | None = None
            try:
                self._queue.put_nowait(alert)
                status = "queued"
            except queue.Full:
                if not self.config.latest_wins:
                    return SubmitOutcome("busy", alert.id)
                try:
                    dropped = self._queue.get_nowait()
                except queue.Empty:
                    dropped = None
                if dropped is not None:
                    replaced = dropped.id
                    if dropped.dedupe_key:
                        self._forget_dedupe(dropped.dedupe_key)
                self._queue.put_nowait(alert)
                status = "replaced" if replaced else "queued"

            self._remember_dedupe(alert)

        return SubmitOutcome(status, alert.id, replaced_id=replaced)

    def run_once(self, alert: AlertRequest) -> AlertResult:
        self._validate(alert)
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        return self._process(alert)

    def status(self) -> dict:
        with self._state_lock:
            last = asdict(self._last) if self._last is not None else None
            busy = self._busy
            model_error = self._model_error

        return {
            "model_ready": tts.is_ready(),
            "model_error": model_error,
            "busy": busy,
            "queue": self._queue.qsize(),
            "uptime_seconds": round(time.monotonic() - self._started, 1),
            "last_alert": last,
        }

    def get_result(self, alert_id: str) -> AlertResult | None:
        with self._state_lock:
            return self._results.get(alert_id)

    def _remember_result(self, result: AlertResult) -> None:
        with self._state_lock:
            self._last = result
            self._results[result.id] = result
            self._results.move_to_end(result.id)
            while len(self._results) > self._results_limit:
                self._results.popitem(last=False)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                alert = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if alert is None:
                break
            try:
                result = self._process_fn(alert)
                if result is not None:
                    self._remember_result(result)
            except Exception:
                self.log.exception(
                    "error no controlado procesando alerta",
                    extra={"event": "alert.unhandled_error", "fields": {"alert_id": alert.id}},
                )

    def _warmup(self) -> None:
        try:
            seconds = tts.warmup(self.config.tts_voice)
            log_event(
                self.log,
                logging.INFO,
                "tts.model_ready",
                "Modelo Kokoro cargado",
                voice=self.config.tts_voice,
                seconds=round(seconds, 3),
            )
        except Exception as exc:
            with self._state_lock:
                self._model_error = f"{type(exc).__name__}: {exc}"
            log_event(
                self.log,
                logging.ERROR,
                "tts.model_failed",
                "No se pudo cargar el modelo Kokoro",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _cleanup_artifacts(self) -> None:
        cutoff = time.time() - self.config.artifact_retention_days * 86400
        alerts_dir = self.config.data_dir / "alerts"
        removed = 0
        try:
            for entry in alerts_dir.iterdir():
                if not entry.is_dir():
                    continue
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
        except OSError as exc:
            log_event(
                self.log,
                logging.WARNING,
                "artifacts.cleanup_failed",
                "No se pudo limpiar artefactos",
                error=str(exc),
            )
            return

        if removed:
            log_event(
                self.log,
                logging.INFO,
                "artifacts.cleaned",
                "Artefactos antiguos eliminados",
                removed=removed,
            )

    def _process(self, alert: AlertRequest) -> AlertResult:
        alert_dir = self.config.data_dir / "alerts" / alert.id
        alert_dir.mkdir(parents=True, exist_ok=True)
        wav_path = alert_dir / "alert.wav"
        webm_path = alert_dir / "alert.webm"

        started_at = _now_iso()
        started = time.perf_counter()
        tts_seconds = 0.0
        audio_seconds = 0.0
        stream_seconds = 0.0
        transport: str | None = None
        error: str | None = None
        sent_audio = False
        status = "failed"

        with self._state_lock:
            self._busy = True

        log_event(
            self.log,
            logging.INFO,
            "alert.received",
            "Alerta recibida",
            alert_id=alert.id,
            kind=alert.kind,
            magnitude=alert.magnitude,
            chars=len(alert.text),
            text=alert.text,
            source=alert.source,
        )

        try:
            t0 = time.perf_counter()
            audio_seconds = tts.synthesize_to_wav(
                alert.text,
                wav_path,
                voice=self.config.tts_voice,
                speed=self.config.tts_speed,
            )
            tts_seconds = time.perf_counter() - t0

            log_event(
                self.log,
                logging.INFO,
                "tts.completed",
                "Audio generado",
                alert_id=alert.id,
                tts_seconds=round(tts_seconds, 3),
                audio_seconds=round(audio_seconds, 2),
                voice=self.config.tts_voice,
            )

            if self.config.dry_run:
                t0 = time.perf_counter()
                audio.encode_webm_file(
                    wav_path,
                    webm_path,
                    bitrate=self.config.audio_bitrate,
                    gain_db=self.config.audio_gain_db,
                    duration_seconds=audio_seconds,
                )
                stream_seconds = time.perf_counter() - t0
                transport = "dry-run"
                status = "dry-run"
                log_event(
                    self.log,
                    logging.INFO,
                    "alert.dry_run",
                    "Audio generado sin transmitir",
                    alert_id=alert.id,
                    wav=str(wav_path),
                    webm=str(webm_path),
                )
            else:
                t0 = time.perf_counter()
                transport = self._stream(alert, wav_path, webm_path, audio_seconds)
                stream_seconds = time.perf_counter() - t0
                sent_audio = True
                status = "ok"
        except TransportError as exc:
            error = str(exc)
            sent_audio = exc.sent_audio
            status = "partial" if sent_audio else "failed"
            log_event(
                self.log,
                logging.ERROR,
                "alert.failed",
                "No se pudo entregar la alerta",
                alert_id=alert.id,
                status=status,
                sent_audio=sent_audio,
                error=error,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.log.exception(
                "fallo inesperado procesando la alerta",
                extra={
                    "event": "alert.failed",
                    "fields": {"alert_id": alert.id, "status": status, "error": error},
                },
            )
        finally:
            with self._state_lock:
                self._busy = False

        if status in ("failed", "dry-run") and alert.dedupe_key:
            self._forget_dedupe(alert.dedupe_key)

        finished_at = _now_iso()
        total_seconds = time.perf_counter() - started

        result = AlertResult(
            id=alert.id,
            status=status,
            kind=alert.kind,
            text=alert.text,
            title=alert.title,
            transport=transport,
            tts_seconds=round(tts_seconds, 3),
            audio_seconds=round(audio_seconds, 2),
            stream_seconds=round(stream_seconds, 3),
            total_seconds=round(total_seconds, 3),
            sent_audio=sent_audio,
            error=error,
            started_at=started_at,
            finished_at=finished_at,
            artifact_dir=str(alert_dir),
            wav_path=str(wav_path) if wav_path.exists() else None,
            webm_path=str(webm_path) if webm_path.exists() else None,
            notify_ok=None,
        )

        should_notify = bool(self.config.discord_webhook_url) and (
            not self.config.dry_run or self.config.notify_in_dry_run
        )
        if should_notify:
            result.notify_ok = notifier.send(
                self.config.discord_webhook_url, alert, result
            )

        self._write_metadata(alert, result)
        self._remember_result(result)

        log_event(
            self.log,
            logging.INFO if status in ("ok", "dry-run") else logging.ERROR,
            "alert.completed",
            "Alerta procesada",
            alert_id=alert.id,
            status=status,
            transport=transport,
            tts_seconds=result.tts_seconds,
            audio_seconds=result.audio_seconds,
            stream_seconds=result.stream_seconds,
            total_seconds=result.total_seconds,
            error=error,
            notify_ok=result.notify_ok,
        )
        return result

    def _stream(
        self,
        alert: AlertRequest,
        wav_path: Path,
        webm_path: Path,
        audio_seconds: float,
    ) -> str:
        order = [self.config.transport]
        if self.config.enable_icecast_fallback:
            order.append("icecast" if self.config.transport == "webdj" else "webdj")

        last_error: TransportError | None = None

        for transport in order:
            try:
                if transport == "webdj":
                    title = alert.title or KIND_TITLES.get(
                        alert.kind, KIND_TITLES["other"]
                    )

                    def chunks():
                        with open(webm_path, "wb") as handle:
                            for chunk in audio.iter_webm_chunks(
                                wav_path,
                                bitrate=self.config.audio_bitrate,
                                gain_db=self.config.audio_gain_db,
                            ):
                                handle.write(chunk)
                                yield chunk

                    webdj.stream(
                        self.config,
                        chunks(),
                        title=title,
                        artist=self.config.metadata_artist,
                    )
                else:
                    icecast.stream(
                        wav_path,
                        self.config,
                        timeout_seconds=audio_seconds + 60,
                    )
                return transport
            except TransportError as exc:
                log_event(
                    self.log,
                    logging.ERROR,
                    "transport.failed",
                    "Transporte falló",
                    alert_id=alert.id,
                    transport=transport,
                    sent_audio=exc.sent_audio,
                    error=str(exc),
                )
                if exc.sent_audio:
                    raise
                last_error = exc

        assert last_error is not None
        raise last_error

    def _write_metadata(self, alert: AlertRequest, result: AlertResult) -> None:
        path = Path(result.artifact_dir) / "meta.json"
        payload = {
            "version": __version__,
            "hostname": socket.gethostname(),
            "alert": asdict(alert),
            "result": asdict(result),
            "config": self.config.safe_summary(),
        }
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log_event(
                self.log,
                logging.WARNING,
                "artifacts.write_failed",
                "No se pudo escribir meta.json",
                alert_id=alert.id,
                error=str(exc),
            )

    def _validate(self, alert: AlertRequest) -> None:
        text = alert.text.strip()
        if not text:
            raise ValueError("El texto de la alerta está vacío")
        if len(text) > self.config.max_text_chars:
            raise ValueError(
                f"El texto supera el máximo de {self.config.max_text_chars} caracteres"
            )
        if alert.kind not in VALID_KINDS:
            raise ValueError(f"kind debe ser uno de {VALID_KINDS}")
        if alert.magnitude is not None and not 0 <= alert.magnitude <= 12:
            raise ValueError("magnitude debe estar entre 0 y 12")
        if len(alert.title) > 200:
            raise ValueError("title supera los 200 caracteres")
        if len(alert.dedupe_key) > 120:
            raise ValueError("dedupe_key supera los 120 caracteres")

    def _find_duplicate(self, alert: AlertRequest) -> str | None:
        if not alert.dedupe_key:
            return None

        now = time.monotonic()
        window = self.config.dedupe_window_seconds
        expired = [key for key, (_, seen) in self._dedupe.items() if now - seen > window]
        for key in expired:
            self._dedupe.pop(key, None)

        entry = self._dedupe.get(alert.dedupe_key)
        return entry[0] if entry else None

    def _remember_dedupe(self, alert: AlertRequest) -> None:
        if alert.dedupe_key:
            self._dedupe[alert.dedupe_key] = (alert.id, time.monotonic())

    def _forget_dedupe(self, key: str) -> None:
        self._dedupe.pop(key, None)
