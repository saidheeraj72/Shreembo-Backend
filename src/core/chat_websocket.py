"""WebSocket manager for chat streaming."""
import json
from typing import Dict, Set, Optional
from uuid import UUID, uuid4
from fastapi import WebSocket

from src.llm.rag import rag_service
from src.chat.service import chat_service
from src.access.limit import limit_service


class ChatConnectionManager:
    """Manages WebSocket connections for chat."""

    def __init__(self):
        # user_id -> set of websockets
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # websocket -> user_id
        self.connection_users: Dict[WebSocket, str] = {}
        # session_id -> set of websockets (for shared sessions)
        self.session_connections: Dict[str, Set[WebSocket]] = {}
        # Active generation tasks that can be cancelled
        self.active_generations: Dict[str, bool] = {}

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str
    ):
        """Accept WebSocket connection."""
        await websocket.accept()

        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()

        self.active_connections[user_id].add(websocket)
        self.connection_users[websocket] = user_id

    def disconnect(self, websocket: WebSocket):
        """Handle WebSocket disconnect."""
        user_id = self.connection_users.get(websocket)

        if user_id and user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

        if websocket in self.connection_users:
            del self.connection_users[websocket]

        # Remove from session connections
        for session_id, connections in list(self.session_connections.items()):
            connections.discard(websocket)
            if not connections:
                del self.session_connections[session_id]

    async def join_session(self, websocket: WebSocket, session_id: str):
        """Join a chat session for real-time updates."""
        if session_id not in self.session_connections:
            self.session_connections[session_id] = set()
        self.session_connections[session_id].add(websocket)

    async def leave_session(self, websocket: WebSocket, session_id: str):
        """Leave a chat session."""
        if session_id in self.session_connections:
            self.session_connections[session_id].discard(websocket)

    async def send_to_user(self, user_id: str, message: dict):
        """Send message to all user's connections."""
        if user_id in self.active_connections:
            msg = json.dumps(message, default=str)
            dead_connections = set()
            for ws in self.active_connections[user_id]:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead_connections.add(ws)
            # Clean up dead connections
            for ws in dead_connections:
                self.active_connections[user_id].discard(ws)

    async def send_to_session(self, session_id: str, message: dict):
        """Send message to all connections in a session."""
        if session_id in self.session_connections:
            msg = json.dumps(message, default=str)
            dead_connections = set()
            for ws in self.session_connections[session_id]:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead_connections.add(ws)
            # Clean up dead connections
            for ws in dead_connections:
                self.session_connections[session_id].discard(ws)

    async def handle_message(
        self,
        websocket: WebSocket,
        user_id: UUID,
        org_id: Optional[UUID],
        data: dict
    ):
        """Handle incoming WebSocket message."""
        msg_type = data.get("type")
        session_id = data.get("session_id")

        if msg_type == "send_message":
            await self.handle_chat_message(
                websocket=websocket,
                user_id=user_id,
                org_id=org_id,
                session_id=UUID(session_id) if session_id else None,
                content=data.get("content", ""),
                rag_enabled=data.get("rag_enabled"),
                web_search_enabled=data.get("web_search_enabled"),
                document_source=data.get("document_source", "organization"),
                selected_document_ids=data.get("selected_document_ids")
            )

        elif msg_type == "stop_generation":
            generation_key = f"{user_id}:{session_id}"
            self.active_generations[generation_key] = False

        elif msg_type == "join_session":
            await self.join_session(websocket, session_id)

        elif msg_type == "leave_session":
            await self.leave_session(websocket, session_id)

    async def handle_chat_message(
        self,
        websocket: WebSocket,
        user_id: UUID,
        org_id: Optional[UUID],
        session_id: UUID,
        content: str,
        rag_enabled: Optional[bool],
        web_search_enabled: Optional[bool],
        document_source: str = "organization",
        selected_document_ids: Optional[list] = None
    ):
        """Process a chat message with streaming response."""
        # Import here to avoid circular dependency
        from src.chat.session_document import session_document_service

        if not session_id or not content:
            await websocket.send_json({
                "type": "stream_error",
                "session_id": str(session_id) if session_id else None,
                "error": "session_id and content are required"
            })
            return

        generation_key = f"{user_id}:{session_id}"
        self.active_generations[generation_key] = True

        try:
            # Get session settings if not overridden
            session = await chat_service.get_session(session_id, user_id, org_id)
            if not session:
                await websocket.send_json({
                    "type": "stream_error",
                    "session_id": str(session_id),
                    "error": "Session not found"
                })
                return

            # Use session defaults if not overridden
            if rag_enabled is None:
                rag_enabled = session["rag_enabled"]
            if web_search_enabled is None:
                web_search_enabled = session["web_search_enabled"]

            # Check usage limits
            limit_checks = await limit_service.check_all_limits(
                user_id=user_id,
                org_id=org_id,
                is_rag=rag_enabled
            )

            # Check if any limit is exceeded
            for check_name, check_result in limit_checks.items():
                if not check_result.allowed:
                    await websocket.send_json({
                        "type": "stream_error",
                        "session_id": str(session_id),
                        "error": check_result.message or f"Usage limit exceeded: {check_name}",
                        "limit_type": check_result.limit_type,
                        "current_usage": check_result.current_usage,
                        "limit_value": check_result.limit_value,
                        "reset_at": check_result.reset_at.isoformat() if check_result.reset_at else None
                    })
                    return

            # Track the request
            await limit_service.track_request(
                user_id=user_id,
                org_id=org_id,
                is_chat=True,
                is_rag=rag_enabled,
                is_web_search=web_search_enabled
            )

            # Save user message
            user_msg = await chat_service.add_message(
                session_id=session_id,
                role="user",
                content=content
            )

            # Update title if this is the first message
            if session["message_count"] == 0:
                new_title = await chat_service.generate_session_title(content)
                await chat_service.update_session(session_id, user_id, title=new_title)

            # Generate ID for the response message ahead of time
            response_message_id = uuid4()

            # Send stream start with the response ID
            await websocket.send_json({
                "type": "stream_start",
                "session_id": str(session_id),
                "message_id": str(response_message_id)
            })

            # Wait for any pending documents if RAG is enabled OR if we are in a session
            # (since RAG off now implies session-only search, we must wait for session docs)
            if rag_enabled or session_id:
                await session_document_service.wait_for_pending_documents(session_id)

            # Generate streaming response
            full_response = ""
            rag_results = []
            web_results = []
            sources = []
            prompt_tokens = 0
            completion_tokens = 0

            # Generate response with RAG
            async for chunk in rag_service.generate_response(
                user_message=content,
                session_id=session_id,
                user_id=user_id,
                org_id=org_id,
                rag_enabled=rag_enabled,
                web_search_enabled=web_search_enabled,
                document_source=document_source,
                selected_document_ids=selected_document_ids
            ):
                # Check if generation was stopped
                if not self.active_generations.get(generation_key, False):
                    await websocket.send_json({
                        "type": "stream_end",
                        "session_id": str(session_id),
                        "content": full_response,
                        "stopped": True
                    })
                    break

                if chunk["type"] == "tool_start":
                    await websocket.send_json({
                        "type": "tool_call",
                        "session_id": str(session_id),
                        "tools": [chunk["name"]],
                        "query": chunk.get("query", ""),
                    })

                elif chunk["type"] == "tool_done":
                    if chunk["name"] == "list_documents" and chunk.get("data"):
                        await websocket.send_json({
                            "type": "document_list",
                            "session_id": str(session_id),
                            "data": chunk["data"],
                        })
                    # other tool_done events: frontend clears tool indicator on rag_context

                elif chunk["type"] == "rag_context":
                    rag_results = chunk["data"]
                    await websocket.send_json({
                        "type": "rag_context",
                        "session_id": str(session_id),
                        "data": rag_results
                    })

                elif chunk["type"] == "web_search":
                    web_results = chunk["data"]
                    await websocket.send_json({
                        "type": "web_search",
                        "session_id": str(session_id),
                        "data": web_results
                    })

                elif chunk["type"] == "reasoning":
                    await websocket.send_json({
                        "type": "stream_reasoning",
                        "session_id": str(session_id),
                        "message_id": str(response_message_id),
                        "content": chunk["content"]
                    })

                elif chunk["type"] == "chunk":
                    full_response += chunk["content"]
                    await websocket.send_json({
                        "type": "stream_chunk",
                        "session_id": str(session_id),
                        "message_id": str(response_message_id),
                        "content": chunk["content"]
                    })

                elif chunk["type"] == "done":
                    sources = chunk.get("sources", [])
                    prompt_tokens = chunk.get("prompt_tokens", 0)
                    completion_tokens = chunk.get("completion_tokens", 0)

                elif chunk["type"] == "error":
                    await websocket.send_json({
                        "type": "stream_error",
                        "session_id": str(session_id),
                        "error": chunk["error"]
                    })
                    return

            # Save assistant message with the pre-generated ID
            assistant_msg = await chat_service.add_message(
                session_id=session_id,
                role="assistant",
                content=full_response,
                message_id=response_message_id,
                rag_context=rag_results,
                web_search_results=web_results,
                sources=sources,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens
            )

            # Send stream end
            await websocket.send_json({
                "type": "stream_end",
                "session_id": str(session_id),
                "message_id": str(response_message_id),
                "content": full_response,
                "sources": sources,
                "token_usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens
                }
            })

        except Exception as e:
            await websocket.send_json({
                "type": "stream_error",
                "session_id": str(session_id),
                "error": str(e)
            })

        finally:
            self.active_generations.pop(generation_key, None)


# Global instance
chat_ws_manager = ChatConnectionManager()
