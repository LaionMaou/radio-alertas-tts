"""Notificación a Discord por webhook (no bloquea el audio)."""

from __future__ import annotations

import logging
import time

import requests

from .models import KIND_LABELS, AlertRequest, AlertResult

log = logging.getLogger(__name__)

COLORS = {
    "eew": 0xC62828,
    "tsunami": 0xEF6C00,
    "typhoon": 0xF9A825,
    "other": 0x546E7A,
}


def _build_payload(alert: AlertRequest, result: AlertResult) -> dict:
    title = KIND_LABELS.get(alert.kind, KIND_LABELS["other"])
    if alert.magnitude is not None:
        title = f"{title} — M{alert.magnitude:g}"
    if alert.title:
        title = f"{title} — {alert.title}"

    fields = [
        {"name": "Estado", "value": result.status, "inline": True},
        {"name": "Transporte", "value": result.transport or "—", "inline": True},
        {"name": "Duración TTS", "value": f"{result.audio_seconds:.1f} s", "inline": True},
        {"name": "Latencia total", "value": f"{result.total_seconds:.1f} s", "inline": True},
        {"name": "Generación TTS", "value": f"{result.tts_seconds:.2f} s", "inline": True},
        {"name": "ID", "value": result.id, "inline": True},
    ]
    if result.error:
        fields.append({"name": "Error", "value": result.error[:1000], "inline": False})

    return {
        "username": "Kuma Radio Alertas",
        "embeds": [
            {
                "title": title[:256],
                "description": alert.text[:3900],
                "color": COLORS.get(alert.kind, COLORS["other"]),
                "timestamp": result.finished_at,
                "fields": fields,
            }
        ],
    }


def send(webhook_url: str, alert: AlertRequest, result: AlertResult) -> bool:
    if not webhook_url:
        return False

    payload = _build_payload(alert, result)

    for attempt in range(1, 4):
        try:
            response = requests.post(
                webhook_url,
                json=payload,
                timeout=8,
                headers={"User-Agent": "kuma-alerts-tts/0.1"},
            )
            if response.status_code < 300:
                log.info(
                    "notificación enviada a Discord",
                    extra={"event": "discord.sent", "fields": {"alert_id": alert.id}},
                )
                return True

            log.warning(
                "Discord respondió con error",
                extra={
                    "event": "discord.http_error",
                    "fields": {
                        "alert_id": alert.id,
                        "status": response.status_code,
                        "attempt": attempt,
                    },
                },
            )
            if response.status_code in (400, 401, 403, 404):
                break
        except requests.RequestException as exc:
            log.warning(
                "fallo de red al notificar a Discord",
                extra={
                    "event": "discord.network_error",
                    "fields": {"alert_id": alert.id, "attempt": attempt, "error": str(exc)},
                },
            )

        if attempt < 3:
            time.sleep(attempt)

    return False
