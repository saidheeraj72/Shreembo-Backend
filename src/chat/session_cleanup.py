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


class SessionDocumentCleanupMixin:
    async def delete_all_session_documents(
        session_id: UUID,
        user_id: UUID
    ) -> bool:
        """
        Delete all documents associated with a session.
        Called when a session is deleted.
        """
        from src.core.qdrant_client import qdrant_client

        # Get all session documents
        session_docs = await SessionDocumentService.get_session_documents(session_id)

        # Delete each document's embeddings and S3 files
        for doc in session_docs:
            session_doc_id = doc.get("id")
            if session_doc_id:
                # Delete embeddings from chat-sessions index
                await qdrant_client.delete_by_document(
                    document_id=session_doc_id,
                    namespace=str(user_id),
                    index_name=settings.QDRANT_SESSIONS_COLLECTION
                )

                # Delete S3 file
                s3_key = doc.get("s3_key")
                if s3_key:
                    try:
                        await s3_client.delete_file(s3_key)
                    except Exception as e:
                        logger.error("Failed to delete S3 file %s: %s", s3_key, e)

        # Delete all session_documents entries
        result = db.admin.table("session_documents").delete().eq(
            "session_id", str(session_id)
        ).execute()

        return True
