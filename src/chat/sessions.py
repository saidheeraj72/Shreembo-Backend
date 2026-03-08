"""Auto-split chat service part."""
from typing import Optional, List
from uuid import UUID
from datetime import datetime

from src.core.database import db
from src.core.openai_client import openai_client
from src.core.exceptions import AuthorizationError


class ChatSessionsMixin:
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
        from src.chat.session_document import session_document_service

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
