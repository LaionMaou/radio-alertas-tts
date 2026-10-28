from __future__ import annotations

from fastapi.testclient import TestClient

from radio_alerts_tts.api import create_app
from radio_alerts_tts.config import load_config
from radio_alerts_tts.service import AlertService


def make_config(tmp_path, **overrides):
    env = {
        "DRY_RUN": "true",
        "DATA_DIR": str(tmp_path),
        "API_TOKEN": "tok_abc123",
    }
    env.update(overrides)
    return load_config(env)


def test_unknown_alert_returns_404(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(AlertService, "start", lambda self, **kwargs: None)
    monkeypatch.setattr(AlertService, "stop", lambda self: None)

    with TestClient(create_app(make_config(tmp_path))) as client:
        response = client.get(
            "/alert/no-existe",
            headers={"Authorization": "Bearer tok_abc123"},
        )

    assert response.status_code == 404


def test_alert_result_requires_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(AlertService, "start", lambda self, **kwargs: None)
    monkeypatch.setattr(AlertService, "stop", lambda self: None)

    with TestClient(create_app(make_config(tmp_path))) as client:
        response = client.get("/alert/no-existe")

    assert response.status_code == 401
