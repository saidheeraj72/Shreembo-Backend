"""Auto-split chat service part."""
from typing import Optional, List, Dict
from uuid import UUID
from datetime import datetime

from src.core.database import db
from src.core.openai_client import openai_client
from src.utils.text_utils import sanitize_text, sanitize_for_db


class ChatMessagesMixin:
    @staticmethod
    async def add_message(
        session_id: UUID,
        role: str,
        content: str,
        message_id: Optional[UUID] = None,
        attachments: Optional[List[dict]] = None,
        rag_context: Optional[List[dict]] = None,
        web_search_results: Optional[List[dict]] = None,
        sources: Optional[List[dict]] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0
    ) -> dict:
        """Add a message to a chat session."""
        # Sanitize all text fields to remove null bytes (PostgreSQL error 22P05)
        data = {
            "session_id": str(session_id),
            "role": role,
            "content": sanitize_text(content) if content else content,
            "attachments": sanitize_for_db(attachments),
            "rag_context": sanitize_for_db(rag_context),
            "web_search_results": sanitize_for_db(web_search_results),
            "sources": sanitize_for_db(sources),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens
        }
        if message_id:
            data["id"] = str(message_id)
            
        result = db.admin.table("chat_messages").insert(data).execute()

        # Update session metadata
        session_result = db.admin.table("chat_sessions").select(
            "message_count"
        ).eq("id", str(session_id)).single().execute()

        current_count = session_result.data.get("message_count", 0) if session_result and session_result.data else 0

        db.admin.table("chat_sessions").update({
            "message_count": current_count + 1,
            "last_message_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", str(session_id)).execute()

        return result.data[0] if result and result.data else None

    @staticmethod
    async def get_session_messages(
        session_id: UUID,
        limit: int = 50,
        before_id: Optional[UUID] = None
    ) -> List[dict]:
        """Get messages for a session with pagination."""
        query = db.admin.table("chat_messages").select("*").eq(
            "session_id", str(session_id)
        )

        if before_id:
            # Get the created_at of the before_id message
            before_msg = db.admin.table("chat_messages").select("created_at").eq(
                "id", str(before_id)
            ).single().execute()
            if before_msg and before_msg.data:
                query = query.lt("created_at", before_msg.data["created_at"])

        result = query.order("created_at", desc=True).limit(limit).execute()
        # Return in chronological order
        return list(reversed(result.data)) if result and result.data else []

    @staticmethod
    async def get_chat_history(
        session_id: UUID,
        limit: int = 20
    ) -> List[Dict[str, str]]:
        """Get formatted chat history for LLM context."""
        messages = await ChatService.get_session_messages(session_id, limit)
        return [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m["role"] in ("user", "assistant")
        ]

    @staticmethod
    async def update_message_tokens(
        message_id: UUID,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int
    ) -> Optional[dict]:
        """Update token counts for a message."""
        result = db.admin.table("chat_messages").update({
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens
        }).eq("id", str(message_id)).execute()
        return result.data[0] if result and result.data else None

    @staticmethod
    async def generate_session_title(content: str) -> str:
        """Generate a title from the first message content."""
        # Simple title generation: take first 50 chars
        title = content[:50].strip()
        if len(content) > 50:
            title += "..."
        return title or "New Chat"
