from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app


def test_ws_progress_accepts_connection_and_ping() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/progress") as websocket:
            websocket.send_json({"type": "subscribe", "novel_id": "novel-1"})
            websocket.send_json({"type": "ping", "nonce": "n-1"})
            message = websocket.receive_json()
            assert message["type"] == "pong"
            assert message["nonce"] == "n-1"
