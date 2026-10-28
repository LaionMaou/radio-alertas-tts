from __future__ import annotations

from urllib.parse import quote

from radio_alerts_tts.icecast import redact_secrets


def test_redacts_credentials_in_url() -> None:
    message = "Error opening output icecast://alert-bot:s3cret@radio:8005/: 401"

    redacted = redact_secrets(message, "s3cret")

    assert "s3cret" not in redacted
    assert "alert-bot" not in redacted
    assert "icecast://***@" in redacted


def test_redacts_url_encoded_password() -> None:
    password = "p@ss:w/rd"
    message = f"fallo en https://host/?pass={quote(password, safe='')}"

    redacted = redact_secrets(message, password)

    assert quote(password, safe="") not in redacted
    assert "***" in redacted


def test_leaves_clean_message_untouched() -> None:
    assert redact_secrets("conexión rechazada", "s3cret") == "conexión rechazada"
