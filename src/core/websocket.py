"""WebSocket manager for real-time updates."""
import json
from typing import Dict, Set
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.connections:
            self.connections[user_id] = set()
        self.connections[user_id].add(websocket)

    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.connections:
            self.connections[user_id].discard(websocket)
            if not self.connections[user_id]:
                del self.connections[user_id]

    async def send_to_user(self, user_id: str, message: dict):
        if user_id in self.connections:
            msg = json.dumps(message)
            for ws in list(self.connections[user_id]):
                try:
                    await ws.send_text(msg)
                except Exception:
                    self.connections[user_id].discard(ws)

    async def send_upload_progress(self, user_id: str, upload_id: str, status: str,
                                    progress: int = 0, document_id: str = None, error: str = None):
        await self.send_to_user(user_id, {
            'type': 'upload_progress',
            'upload_id': upload_id,
            'status': status,
            'progress': progress,
            'document_id': document_id,
            'error': error
        })


ws_manager = ConnectionManager()
