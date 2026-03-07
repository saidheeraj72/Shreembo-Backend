"""Chat service for session and message management."""
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime

from src.core.database import db
from src.core.exceptions import NotFoundError, AuthorizationError
from src.utils.text_utils import sanitize_text, sanitize_for_db


class ChatService:
    """Service for chat session and message CRUD operations."""



    # ============== Session Operations ==============

    @staticmethod
    async def create_session(
        user_id: UUID,
        org_id: Optional[UUID],
        title: str = "New Chat",
        rag_enabled: bool = True,
        web_search_enabled: bool = False
    ) -> dict:
        """Create a new chat session."""
        data = {
            "user_id": str(user_id),
            "org_id": str(org_id) if org_id else None,
            "title": title,
            "rag_enabled": rag_enabled,
            "web_search_enabled": web_search_enabled,
            "status": "active",
            "message_count": 0
        }
        result = db.admin.table("chat_sessions").insert(data).execute()
        return result.data[0] if result and result.data else None

    @staticmethod
    async def get_session(
        session_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID]
    ) -> Optional[dict]:
        """Get a chat session with access check."""
        result = db.admin.table("chat_sessions").select("*").eq(
            "id", str(session_id)
        ).maybe_single().execute()

        if not result or not result.data:
            return None

        session = result.data

        # Access check: owner OR shared within org
        if str(session["user_id"]) != str(user_id):
            if not (org_id and session.get("org_id") == str(org_id) and session.get("is_shared")):
                raise AuthorizationError("Access denied to this chat session")

        return session

    @staticmethod
    async def get_user_sessions(
        user_id: UUID,
        org_id: Optional[UUID],
        include_shared: bool = True,
        limit: int = 50,
        offset: int = 0
    ) -> List[dict]:
        """Get user's chat sessions and optionally shared sessions."""
        sessions = []

        # User's own sessions
        query = db.admin.table("chat_sessions").select("*").eq(
            "user_id", str(user_id)
        ).neq("status", "deleted")

        if org_id:
            query = query.eq("org_id", str(org_id))

        own_result = query.order("updated_at", desc=True).range(
            offset, offset + limit - 1
        ).execute()

        sessions = own_result.data if own_result and own_result.data else []

        # Include shared sessions from org
        if include_shared and org_id:
            shared_query = db.admin.table("chat_sessions").select("*").eq(
                "org_id", str(org_id)
            ).eq("is_shared", True).neq("status", "deleted").neq(
                "user_id", str(user_id)
            ).order("updated_at", desc=True).limit(20).execute()

            if shared_query and shared_query.data:
                sessions.extend(shared_query.data)

        return sessions

    @staticmethod
    async def update_session(
        session_id: UUID,
        user_id: UUID,
        **updates
    ) -> Optional[dict]:
        """Update chat session settings."""
        updates["updated_at"] = datetime.utcnow().isoformat()
        result = db.admin.table("chat_sessions").update(updates).eq(
            "id", str(session_id)
        ).eq("user_id", str(user_id)).execute()
        return result.data[0] if result and result.data else None

    @staticmethod
    async def delete_session(session_id: UUID, user_id: UUID) -> bool:
        """Soft delete a chat session and clean up session documents."""
        # Import here to avoid circular dependency
        from src.services.session_document_service import session_document_service

        # Clean up all session documents and their embeddings
        await session_document_service.delete_all_session_documents(session_id, user_id)

        # Soft delete the session
        result = db.admin.table("chat_sessions").update({
            "status": "deleted",
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", str(session_id)).eq("user_id", str(user_id)).execute()
        return bool(result and result.data)

    @staticmethod
    async def share_session(
        session_id: UUID,
        user_id: UUID,
        org_id: UUID
    ) -> Optional[dict]:
        """Share session with organization members."""
        return await ChatService.update_session(
            session_id, user_id,
            is_shared=True,
            shared_at=datetime.utcnow().isoformat(),
            shared_by=str(user_id)
        )

    @staticmethod
    async def unshare_session(
        session_id: UUID,
        user_id: UUID
    ) -> Optional[dict]:
        """Unshare a session."""
        return await ChatService.update_session(
            session_id, user_id,
            is_shared=False,
            shared_at=None,
            shared_by=None
        )

    # ============== Message Operations ==============

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


chat_service = ChatService()
