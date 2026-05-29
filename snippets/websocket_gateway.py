"""WebSocket connection manager.

Handles connection lifecycle, per-connection channel subscriptions, and
concurrent fan-out to all subscribed clients.

Design notes:
- asyncio.gather with return_exceptions=True: one slow or dead client
  never blocks delivery to the remaining N-1 clients.
- Snapshot provider: on subscribe, delivers the last known state for
  that channel immediately, so new clients don't see an empty UI.
- Connection limit enforced at accept time to prevent resource exhaustion.
"""

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

AVAILABLE_CHANNELS = frozenset(
    ["market", "analysis", "ai_stream", "trading", "replay", "system"]
)

router = APIRouter(tags=["websocket"])


class WebSocketGateway:
    def __init__(self, max_connections: int = 100, snapshot_provider=None) -> None:
        self._max_connections = max_connections
        self._connections: dict[str, WebSocket] = {}
        self._subscriptions: dict[str, set[str]] = {}
        self._snapshot_provider = snapshot_provider

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def connect(self, client_id: str, websocket: WebSocket) -> bool:
        if len(self._connections) >= self._max_connections:
            await websocket.close(code=1013, reason="Connection limit reached")
            return False

        await websocket.accept()
        self._connections[client_id] = websocket
        self._subscriptions[client_id] = set()

        await self._send(client_id, {
            "event": "system.connected",
            "channel": "system",
            "payload": {
                "client_id": client_id,
                "available_channels": sorted(AVAILABLE_CHANNELS),
            },
        })
        logger.info(f"WS connected: {client_id} (total={len(self._connections)})")
        return True

    async def disconnect(self, client_id: str) -> None:
        self._connections.pop(client_id, None)
        self._subscriptions.pop(client_id, None)

    async def handle_message(self, client_id: str, data: dict[str, Any]) -> None:
        action = data.get("action")

        if action == "subscribe":
            channels = set(data.get("channels", [])) & AVAILABLE_CHANNELS
            new_channels = channels - self._subscriptions.get(client_id, set())
            self._subscriptions[client_id].update(channels)

            if new_channels and self._snapshot_provider:
                for ch in sorted(new_channels):
                    try:
                        snapshot = await self._snapshot_provider(ch)
                        if snapshot:
                            await self._send(client_id, snapshot)
                    except Exception as exc:
                        logger.warning(f"Cold-start snapshot [{ch}] failed: {exc}")

        elif action == "unsubscribe":
            channels = set(data.get("channels", []))
            if channels:
                self._subscriptions[client_id].difference_update(channels)

        elif action == "pong":
            pass

    async def broadcast_to_channel(self, channel: str, message: dict[str, Any]) -> None:
        targets = [
            cid
            for cid, channels in self._subscriptions.items()
            if channel in channels
        ]
        if not targets:
            return

        # Fan-out concurrently — one slow client doesn't block others.
        # return_exceptions=True collects failures rather than raising on the first one.
        await asyncio.gather(
            *[self._send(cid, message) for cid in targets],
            return_exceptions=True,
        )

    async def ping_all(self) -> None:
        message = {"event": "system.ping", "channel": "system", "payload": {}}
        await asyncio.gather(
            *[self._send(cid, message) for cid in list(self._connections)],
            return_exceptions=True,
        )

    async def _send(self, client_id: str, message: dict[str, Any]) -> None:
        websocket = self._connections.get(client_id)
        if not websocket:
            return
        try:
            await websocket.send_json(message)
        except Exception as exc:
            logger.warning(f"WS send failed for {client_id}: {exc} — disconnecting")
            await self.disconnect(client_id)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    gateway: WebSocketGateway = websocket.app.state.ws_gateway
    client_id = str(uuid.uuid4())

    connected = await gateway.connect(client_id, websocket)
    if not connected:
        return

    async def heartbeat() -> None:
        import asyncio as _asyncio
        from oracle.config.settings import get_settings
        interval = get_settings().WS_HEARTBEAT_INTERVAL
        while True:
            await _asyncio.sleep(interval)
            if client_id not in gateway._connections:
                break
            await gateway._send(
                client_id,
                {"event": "system.ping", "channel": "system", "payload": {}},
            )

    heartbeat_task = asyncio.create_task(heartbeat())

    try:
        while True:
            data = await websocket.receive_json()
            await gateway.handle_message(client_id, data)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error(f"WS error for {client_id}: {exc}")
    finally:
        heartbeat_task.cancel()
        await gateway.disconnect(client_id)
