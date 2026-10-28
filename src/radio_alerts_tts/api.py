"""API HTTP local para recibir alertas."""

from __future__ import annotations

import hmac
import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import VALID_KINDS, Config, load_config
from .errors import ConfigError
from .logging_setup import force_utf8_stdio, setup_logging
from .models import VALID_TONES, AlertRequest
from .service import AlertService

log = logging.getLogger("radio_alerts_tts")


class AlertIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    kind: str = "other"
    magnitude: float | None = Field(default=None, ge=0, le=12)
    title: str = Field(default="", max_length=200)
    dedupe_key: str = Field(default="", max_length=120)
    tone: str = "soft"
    loop: bool = False
    preempt: bool = False


def create_app(config: Config) -> FastAPI:
    service = AlertService(config, log)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        service.start()
        try:
            yield
        finally:
            service.stop()

    app = FastAPI(
        title="Kuma Radio — Alertas TTS",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def require_auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = config.api_token
        if not expected:
            raise HTTPException(500, "API_TOKEN no configurado")
        if not authorization or not hmac.compare_digest(
            authorization, f"Bearer {expected}"
        ):
            raise HTTPException(
                401,
                "No autorizado",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.post("/alert", status_code=202, dependencies=[Depends(require_auth)])
    def create_alert(payload: AlertIn):
        if payload.kind not in VALID_KINDS:
            raise HTTPException(422, f"kind debe ser uno de {VALID_KINDS}")
        if len(payload.text) > config.max_text_chars:
            raise HTTPException(
                422,
                f"text supera el máximo de {config.max_text_chars} caracteres",
            )
        if payload.tone not in VALID_TONES:
            raise HTTPException(422, f"tone debe ser uno de {sorted(VALID_TONES)}")

        alert = AlertRequest(
            text=payload.text,
            kind=payload.kind,
            magnitude=payload.magnitude,
            title=payload.title,
            dedupe_key=payload.dedupe_key,
            tone=payload.tone,
            loop=payload.loop,
            preempt=payload.preempt,
            source="api",
        )

        try:
            outcome = service.submit(alert)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        body = {"alert_id": outcome.alert_id, "status": outcome.status}
        if outcome.replaced_id:
            body["replaced_id"] = outcome.replaced_id
        if outcome.duplicate_of:
            body["duplicate_of"] = outcome.duplicate_of

        if outcome.status == "busy":
            raise HTTPException(429, "Ya hay una alerta en curso; reintentar")
        if outcome.status == "duplicate":
            return JSONResponse(status_code=200, content=body)
        return JSONResponse(status_code=202, content=body)

    @app.post("/stop", dependencies=[Depends(require_auth)])
    def stop_alert():
        stopped = service.stop_active()
        return {"stopped": stopped, "status": "stopping" if stopped else "idle"}

    @app.get("/alert/{alert_id}", dependencies=[Depends(require_auth)])
    def get_alert(alert_id: str):
        result = service.get_result(alert_id)
        if result is None:
            raise HTTPException(404, "Alerta no encontrada")
        return asdict(result)

    @app.get("/health")
    def health():
        data = service.status()
        data["version"] = __version__
        if data.get("model_error"):
            return JSONResponse(
                status_code=503,
                content={"status": "degraded", **data},
            )
        if not data.get("model_ready"):
            return JSONResponse(
                status_code=503,
                content={"status": "starting", **data},
            )
        data["status"] = "ok"
        return data

    return app


def main() -> int:
    force_utf8_stdio()
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        return 2

    setup_logging(config.log_level)

    if not config.api_token:
        log.error(
            "API_TOKEN no configurado; la API no arranca",
            extra={"event": "api.config_error", "fields": {}},
        )
        return 2

    uvicorn.run(
        create_app(config),
        host=config.api_host,
        port=config.api_port,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
