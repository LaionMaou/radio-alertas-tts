"""Logs estructurados en una línea JSON por evento."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, TextIO


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def force_utf8(stream: TextIO) -> None:
    """Evita UnicodeEncodeError con texto japonés en consolas Windows."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass


def force_utf8_stdio() -> None:
    force_utf8(sys.stdout)
    force_utf8(sys.stderr)


def setup_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    target = stream or sys.stdout
    force_utf8(target)
    handler = logging.StreamHandler(target)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for noisy in ("uvicorn", "uvicorn.error", "websockets", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str | None = None,
    **fields: Any,
) -> None:
    logger.log(level, message or event, extra={"event": event, "fields": fields})
