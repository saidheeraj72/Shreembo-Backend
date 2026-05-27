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


class SessionDocumentReprocessMixin:
    @staticmethod
    def _reprocess_document_background_sync(
        session_document_id: UUID,
        org_id: Optional[UUID],
        s3_key: str,
        file_type: str,
        user_id: str,
        session_id: str,
        filename: str
    ):
        """Synchronous wrapper for reprocessing background task."""
        asyncio.run(SessionDocumentService._reprocess_document_background(
            session_document_id=session_document_id,
            org_id=org_id,
            s3_key=s3_key,
            file_type=file_type,
            user_id=user_id,
            session_id=session_id,
            filename=filename
        ))

    @staticmethod
    async def _reprocess_document_background(
        session_document_id: UUID,
        org_id: Optional[UUID],
        s3_key: str,
        file_type: str,
        user_id: str,
        session_id: str,
        filename: str
    ):
        """Background task for reprocessing existing document embeddings."""
        from src.core.qdrant_client import qdrant_client

        logger.info(f"Reprocessing embeddings for session document {session_document_id}")

        try:
            # Delete existing embeddings first
            await qdrant_client.delete_by_document(
                document_id=str(session_document_id),
                namespace=user_id,
                index_name=settings.QDRANT_SESSIONS_COLLECTION
            )

            # Process embeddings again
            await embedding_service.process_document(
                document_id=session_document_id,
                org_id=org_id,
                s3_key=s3_key,
                file_type=file_type,
                user_id=user_id,
                upload_id=str(session_document_id),
                is_session_document=True,
                session_id=session_id
            )

            # Update completion status
            db.admin.table("session_documents").update({
                "embedding_status": "completed",
                "processed_at": datetime.utcnow().isoformat()
            }).eq("id", str(session_document_id)).execute()

            logger.info(f"Successfully reprocessed session document {session_document_id}")

            # Send WebSocket notification
            await chat_ws_manager.send_to_user(user_id, {
                "type": "session_document_ready",
                "session_id": session_id,
                "document_id": str(session_document_id),
                "filename": filename,
                "embedding_status": "completed"
            })

        except Exception as e:
            logger.error(f"Reprocessing failed for session document {session_document_id}: {e}", exc_info=True)

            # Revert status back to completed (since document already exists)
            db.admin.table("session_documents").update({
                "embedding_status": "completed"
            }).eq("id", str(session_document_id)).execute()

            # Send WebSocket notification about failure
            await chat_ws_manager.send_to_user(user_id, {
                "type": "session_document_ready",
                "session_id": session_id,
                "document_id": str(session_document_id),
                "filename": filename,
                "embedding_status": "failed",
                "error": str(e)
            })

    @staticmethod
    async def reprocess_document(
        session_document_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID],
        background_tasks: Optional["BackgroundTasks"] = None
    ) -> dict:
        """
        Reprocess a session document to regenerate embeddings.
        Useful for refreshing embeddings for an existing document.
        """
        # Get session document
        session_doc_result = db.admin.table("session_documents").select(
            "*"
        ).eq("id", str(session_document_id)).single().execute()

        if not session_doc_result.data:
            raise ValueError("Session document not found")

        session_doc = session_doc_result.data

        # Verify user owns this document
        if session_doc["user_id"] != str(user_id):
            raise ValueError("Access denied")

        # Check if file type supports embeddings
        file_type = session_doc.get("file_type", "").lower()
        supported_types = settings.SUPPORTED_EMBEDDING_TYPES

        if file_type not in supported_types:
            raise ValueError("File type does not support embeddings")

        logger.info(f"Reprocessing session document {session_document_id}")

        # Always use async background processing to stay in main event loop
        if background_tasks:
            background_tasks.add_task(
                SessionDocumentService._reprocess_document_background,
                session_document_id=session_document_id,
                org_id=org_id,
                s3_key=session_doc["s3_key"],
                file_type=file_type,
                user_id=str(user_id),
                session_id=session_doc["session_id"],
                filename=session_doc["filename"]
            )
        else:
            asyncio.create_task(
                SessionDocumentService._reprocess_document_background(
                    session_document_id=session_document_id,
                    org_id=org_id,
                    s3_key=session_doc["s3_key"],
                    file_type=file_type,
                    user_id=str(user_id),
                    session_id=session_doc["session_id"],
                    filename=session_doc["filename"]
                )
            )

        return session_doc

    @staticmethod
    async def wait_for_pending_documents(session_id: UUID, timeout: int = 20) -> bool:
        """
        Wait for any pending document uploads in the session to complete.
        Returns True if all documents are ready, False if timeout reached.
        """
        logger.info(f"Waiting for pending documents in session {session_id} (timeout={timeout}s)")
        start_time = datetime.utcnow()
        while (datetime.utcnow() - start_time).total_seconds() < timeout:
            # Check for any processing attachments in recent messages
            try:
                result = db.admin.table("chat_messages").select("attachments").eq(
                    "session_id", str(session_id)
                ).order("created_at", desc=True).limit(10).execute()

                if not result.data:
                    logger.info(f"No messages found for session {session_id} while waiting for docs")
                    return True

                has_processing = False
                processing_files = []
                for msg in result.data:
                    attachments = msg.get("attachments") or []
                    for att in attachments:
                        if att.get("status") == "processing":
                            has_processing = True
                            processing_files.append(att.get("filename", "unknown"))
                    if has_processing:
                        break
                
                if not has_processing:
                    logger.info(f"All documents in session {session_id} are ready")
                    return True
                
                logger.info(f"Still waiting for {len(processing_files)} documents in session {session_id}: {processing_files}")
            except Exception as e:
                logger.error(f"Error checking pending documents: {e}")
            
            # Wait a bit before checking again
            await asyncio.sleep(1.5)
            
        logger.warning(f"Timeout reached waiting for documents in session {session_id}")
        return False

