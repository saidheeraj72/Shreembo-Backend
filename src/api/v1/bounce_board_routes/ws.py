"""
Bounce Board — WebSocket for live session updates.

  WS /sessions/{session_id}/ws?token=<jwt>

Server → client events (each carries full state, never deltas):
  {"type": "session",    "session": <Session>}          on every stage/row change
  {"type": "discussion", "discussion": <DiscussionState>}  per board message

Auth mirrors the chat WebSocket: Supabase JWT as a query parameter.
"""
import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.bounce_board import events, store
from src.core.database import db
from src.core.security import verify_supabase_jwt

logger = logging.getLogger(__name__)

router = APIRouter()


async def _forward(queue: asyncio.Queue, websocket: WebSocket) -> None:
    while True:
        event = await queue.get()
        await websocket.send_json(event)


@router.websocket("/sessions/{session_id}/ws")
async def session_events(websocket: WebSocket, session_id: str) -> None:
    token = websocket.query_params.get("token")
    payload = verify_supabase_jwt(token) if token else None
    if not payload:
        await websocket.close(code=4001, reason="Invalid or missing token")
        return
    user_id = UUID(payload["sub"])

    result = (
        db.admin.table(store.TABLE)
        .select("*")
        .eq("id", session_id)
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        await websocket.close(code=4004, reason="Session not found")
        return
    row = result.data

    await websocket.accept()
    queue = events.subscribe(session_id)
    forwarder = asyncio.create_task(_forward(queue, websocket))
    try:
        # Initial snapshot so the client never misses state between the HTTP
        # load and the socket opening.
        await websocket.send_json({"type": "session", "session": store.session_to_api(row)})
        if row.get("discussion"):
            await websocket.send_json({"type": "discussion", "discussion": row["discussion"]})
        while True:
            # Clients don't send data; this loop only detects disconnects
            # (and tolerates pings/keepalives).
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — connection-level failures just end the socket
        logger.exception("Bounce board WebSocket error for session %s", session_id)
    finally:
        forwarder.cancel()
        events.unsubscribe(session_id, queue)
