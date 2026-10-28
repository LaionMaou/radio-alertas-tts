from __future__ import annotations

import json
import socket
import threading
from dataclasses import replace

import pytest
from websockets.sync.server import serve

from radio_alerts_tts import webdj
from radio_alerts_tts.config import load_config
from radio_alerts_tts.errors import TransportError


def make_config(tmp_path, ws_url: str):
    return load_config(
        {
            "AZURACAST_BASE_URL": "https://radio.example.com",
            "AZURACAST_STATION_SHORT_NAME": "kuma",
            "AZURACAST_STREAMER_USER": "alert-bot",
            "AZURACAST_STREAMER_PASSWORD": "s3cret",
            "AZURACAST_DJ_HOST": "127.0.0.1",
            "AZURACAST_WS_URL": ws_url,
            "DRAIN_SECONDS": "0",
            "DATA_DIR": str(tmp_path),
        }
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_stream_sends_hello_metadata_and_audio(tmp_path) -> None:
    received: list[str | bytes] = []
    finished = threading.Event()

    def handler(ws) -> None:
        try:
            for message in ws:
                received.append(message)
        finally:
            finished.set()

    server = serve(handler, "127.0.0.1", 0, subprotocols=["webcast"])
    port = server.socket.getsockname()[1]
    server.socket.listen()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    payload = b"\x1aE\xdf\xa3carga-webm"
    try:
        config = make_config(tmp_path, f"ws://127.0.0.1:{port}/")
        webdj.stream(
            config,
            iter([payload]),
            title="緊急地震速報",
            artist="Kuma Radio",
        )
        assert finished.wait(5)
    finally:
        server.shutdown()

    assert len(received) == 3

    hello = json.loads(received[0])
    assert hello["type"] == "hello"
    assert hello["data"]["mime"] == "audio/webm;codecs=opus"
    assert hello["data"]["user"] == "alert-bot"
    assert hello["data"]["password"] == "s3cret"

    metadata = json.loads(received[1])
    assert metadata["type"] == "metadata"
    assert metadata["data"]["title"] == "緊急地震速報"
    assert metadata["data"]["artist"] == "Kuma Radio"

    assert received[2] == payload


def test_connection_failure_is_wrapped(tmp_path) -> None:
    port = free_port()
    config = replace(
        make_config(tmp_path, f"ws://127.0.0.1:{port}/"), open_timeout=1.0
    )

    with pytest.raises(TransportError) as info:
        webdj.stream(config, iter([b"x"]), title="t", artist="a")

    assert info.value.sent_audio is False
