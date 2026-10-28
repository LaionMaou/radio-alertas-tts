"""Cliente del WebSocket del WebDJ de AzuraCast (subprotocolo ``webcast``).

Secuencia real que usa el navegador (AzuraCast frontend/components/Public/WebDJ):
1. Abrir ``wss://host/webdj/<short_name><dj_mount>`` con subprotocolo ``webcast``.
2. Enviar JSON ``{"type":"hello","data":{"mime":..., "user":..., "password":...}}``.
3. Enviar (opcional) JSON de metadatos.
4. Enviar el audio como mensajes binarios (WebM/Opus).
5. Cerrar el socket para que el AutoDJ retome.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
from typing import Iterable

from websockets.sync.client import connect

from .config import Config
from .errors import TransportError

MIME_WEBM_OPUS = "audio/webm;codecs=opus"

log = logging.getLogger(__name__)


def _ssl_context(config: Config) -> ssl.SSLContext | None:
    if not config.ws_url.startswith("wss://"):
        return None
    context = ssl.create_default_context()
    if not config.tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def stream(
    config: Config,
    chunks: Iterable[bytes],
    *,
    title: str,
    artist: str,
) -> None:
    url = config.ws_url
    if not url:
        raise TransportError("WebDJ: falta la URL del socket")

    sent_audio = False
    sent_bytes = 0

    try:
        with connect(
            url,
            subprotocols=["webcast"],
            open_timeout=config.open_timeout,
            close_timeout=5,
            max_size=None,
            ping_interval=None,
            ssl=_ssl_context(config),
        ) as socket:
            log.info(
                "socket WebDJ conectado",
                extra={
                    "event": "webdj.connected",
                    "fields": {"url": url, "subprotocol": socket.subprotocol},
                },
            )

            hello = {
                "type": "hello",
                "data": {
                    "mime": MIME_WEBM_OPUS,
                    "user": config.streamer_user,
                    "password": config.streamer_password,
                },
            }
            socket.send(json.dumps(hello, ensure_ascii=False))
            socket.send(
                json.dumps(
                    {"type": "metadata", "data": {"title": title, "artist": artist}},
                    ensure_ascii=False,
                )
            )

            for chunk in chunks:
                socket.send(chunk)
                sent_audio = True
                sent_bytes += len(chunk)

            if config.drain_seconds > 0:
                time.sleep(config.drain_seconds)

        log.info(
            "socket WebDJ cerrado",
            extra={
                "event": "webdj.closed",
                "fields": {"bytes": sent_bytes, "sent_audio": sent_audio},
            },
        )
    except Exception as exc:
        raise TransportError(
            f"WebDJ: {type(exc).__name__}: {exc}",
            sent_audio=sent_audio,
        ) from exc
