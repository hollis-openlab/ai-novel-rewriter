from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState


class WsHub:
    """In-memory WebSocket subscription hub for local development/runtime."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subscriptions: dict[WebSocket, set[str]] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._subscriptions[websocket] = set()

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscriptions.pop(websocket, None)

    async def subscribe(self, websocket: WebSocket, novel_id: str) -> None:
        novel_id = str(novel_id).strip()
        if not novel_id:
            return
        async with self._lock:
            subscriptions = self._subscriptions.get(websocket)
            if subscriptions is not None:
                subscriptions.add(novel_id)

    async def unsubscribe(self, websocket: WebSocket, novel_id: str) -> None:
        novel_id = str(novel_id).strip()
        if not novel_id:
            return
        async with self._lock:
            subscriptions = self._subscriptions.get(websocket)
            if subscriptions is not None:
                subscriptions.discard(novel_id)

    async def publish(self, message: Mapping[str, Any]) -> None:
        payload = dict(message)
        target_novel_id = payload.get("novel_id")
        target = str(target_novel_id).strip() if target_novel_id is not None else None

        async with self._lock:
            recipients = list(self._subscriptions.items())

        stale_connections: list[WebSocket] = []
        for websocket, subscriptions in recipients:
            if websocket.application_state != WebSocketState.CONNECTED:
                stale_connections.append(websocket)
                continue

            if target is not None:
                if "*" not in subscriptions and target not in subscriptions:
                    continue

            try:
                await websocket.send_json(payload)
            except Exception:
                stale_connections.append(websocket)

        if stale_connections:
            async with self._lock:
                for websocket in stale_connections:
                    self._subscriptions.pop(websocket, None)
