"""Auto-split session document service part."""
import asyncio
import logging
from typing import Optional, List, TYPE_CHECKING
from uuid import UUID, uuid4
from datetime import datetime

from src.core.database import db
from src.core.s3 import s3_client
from src.core.chat_websocket import chat_ws_manager
from src.llm.embedding import embedding_service
from src.config import settings

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)


class SessionDocumentOpsMixin:
    @staticmethod
    async def get_session_documents(session_id: UUID) -> List[dict]:
        """Get all documents for a session."""
        result = db.admin.table("session_documents").select(
            "*"
        ).eq(
            "session_id", str(session_id)
        ).order("uploaded_at").execute()

        return result.data if result.data else []

    @staticmethod
    async def get_session_document(session_document_id: UUID) -> Optional[dict]:
        """Get a single session document."""
        result = db.admin.table("session_documents").select(
            "*"
        ).eq("id", str(session_document_id)).single().execute()

        return result.data if result.data else None

    @staticmethod
    async def delete_session_document(
        session_document_id: UUID,
        user_id: UUID
    ) -> bool:
        """
        Delete a session document.

        This removes the session document and cleans up embeddings from chat-sessions index.
        """
        from src.core.qdrant_client import qdrant_client

        # Get document info before deleting
        session_doc = await SessionDocumentService.get_session_document(session_document_id)

        if not session_doc or session_doc.get("user_id") != str(user_id):
            return False

        # Delete from session_documents table
        result = db.admin.table("session_documents").delete().eq(
            "id", str(session_document_id)
        ).eq("user_id", str(user_id)).execute()

        if result.data:
            # Clean up embeddings from chat-sessions index
            # Use session_document_id as the document_id
            await qdrant_client.delete_by_document(
                document_id=str(session_document_id),
                namespace=str(user_id),
                index_name=settings.QDRANT_SESSIONS_COLLECTION
            )

            # Clean up S3 file
            s3_key = session_doc.get("s3_key")
            if s3_key:
                try:
                    await s3_client.delete_file(s3_key)
                except Exception as e:
                    logger.error("Failed to delete S3 file %s: %s", s3_key, e)

        return bool(result.data)

    @staticmethod
    async def get_session_document_ids(session_id: UUID) -> List[str]:
        """Get session document IDs for a session (for RAG filtering)."""
        result = db.admin.table("session_documents").select(
            "id"
        ).eq("session_id", str(session_id)).eq(
            "embedding_status", "completed"
        ).execute()

        return [d["id"] for d in result.data] if result.data else []

