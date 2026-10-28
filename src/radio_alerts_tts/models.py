"""Modelos de datos compartidos entre API, CLI y servicio."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

KIND_TITLES = {
    "eew": "緊急地震速報",
    "tsunami": "津波警報",
    "typhoon": "台風情報",
    "other": "お知らせ",
}

KIND_LABELS = {
    "eew": "Alerta sísmica (EEW)",
    "tsunami": "Alerta de tsunami",
    "typhoon": "Aviso de tifón",
    "other": "Aviso",
}


def new_alert_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:6]}"


@dataclass
class AlertRequest:
    text: str
    kind: str = "other"
    magnitude: float | None = None
    title: str = ""
    dedupe_key: str = ""
    source: str = "api"
    id: str = field(default_factory=new_alert_id)


@dataclass
class AlertResult:
    id: str
    status: str
    kind: str
    text: str
    title: str
    transport: str | None
    tts_seconds: float
    audio_seconds: float
    stream_seconds: float
    total_seconds: float
    sent_audio: bool
    error: str | None
    started_at: str
    finished_at: str
    artifact_dir: str
    wav_path: str | None
    webm_path: str | None
    notify_ok: bool | None


@dataclass
class SubmitOutcome:
    status: str
    alert_id: str
    replaced_id: str | None = None
    duplicate_of: str | None = None
