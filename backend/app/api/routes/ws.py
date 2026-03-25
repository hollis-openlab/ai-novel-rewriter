from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from backend.app.services.ws_hub import WsHub

router = APIRouter(tags=["ws"])


@router.websocket("/ws/progress")
async def progress_socket(websocket: WebSocket) -> None:
    hub = getattr(websocket.app.state, "ws_hub", None)
    if not isinstance(hub, WsHub):
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    await hub.connect(websocket)
    try:
        while True:
            payload = await websocket.receive_json()
            message_type = str(payload.get("type") or "").strip().lower()

            if message_type == "subscribe":
                await hub.subscribe(websocket, str(payload.get("novel_id") or ""))
                continue

            if message_type == "unsubscribe":
                await hub.unsubscribe(websocket, str(payload.get("novel_id") or ""))
                continue

            if message_type == "ping":
                await websocket.send_json({"type": "pong", "nonce": payload.get("nonce")})
                continue

            # Ignore unsupported client events for forward compatibility.
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.disconnect(websocket)
