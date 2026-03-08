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


class SessionDocumentProcessingMixin:
    async def _process_document_background(
        org_id: Optional[UUID],
        s3_key: str,
        file_type: str,
        user_id: str,
        session_id: str,
        filename: str,
        file_size: int,
        mime_type: str
    ):
        """
        Background task for processing document embeddings.
        This runs asynchronously without blocking the HTTP response.
        Document is only inserted into session_documents after successful processing.
        """
        # Generate a new session_document_id for this document
        session_document_id = uuid4()
        logger.info(f"Starting embedding processing for session document {session_document_id}")

        try:
            # Process embeddings for session document (uses chat-sessions index)
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

            # Only insert into session_documents after successful processing
            session_doc_data = {
                "id": str(session_document_id),
                "session_id": session_id,
                "user_id": user_id,
                "filename": filename,
                "file_type": file_type,
                "file_size": file_size,
                "s3_key": s3_key,
                "mime_type": mime_type,
                "embedding_status": "completed",
                "processed_at": datetime.utcnow().isoformat()
            }
            db.admin.table("session_documents").insert(session_doc_data).execute()

            # Update the chat message attachment with session_document_id and completed status
            await SessionDocumentService._update_message_attachment(
                session_id=session_id,
                filename=filename,
                session_document_id=str(session_document_id),
                status="completed"
            )

            logger.info(f"Successfully completed embedding processing for session document {session_document_id}")

            # Send WebSocket notification about completion
            await chat_ws_manager.send_to_user(user_id, {
                "type": "session_document_ready",
                "session_id": session_id,
                "document_id": str(session_document_id),
                "filename": filename,
                "embedding_status": "completed"
            })

        except Exception as e:
            logger.error(f"Background processing failed for session document {session_document_id}: {e}", exc_info=True)

            # Update the chat message attachment with failed status
            await SessionDocumentService._update_message_attachment(
                session_id=session_id,
                filename=filename,
                session_document_id=str(session_document_id),
                status="failed"
            )

            # Send WebSocket notification about failure (no DB record created)
            await chat_ws_manager.send_to_user(user_id, {
                "type": "session_document_ready",
                "session_id": session_id,
                "document_id": str(session_document_id),
                "filename": filename,
                "embedding_status": "failed",
                "error": str(e)
            })

    @staticmethod
    async def complete_upload(
        session_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID],
        s3_key: str,
        filename: str,
        file_type: str,
        file_size: int,
        mime_type: str,
        background_tasks: Optional["BackgroundTasks"] = None
    ) -> dict:
        """
        Complete document upload and trigger embedding in background.

        Returns immediately - processing continues via WebSocket progress updates.
        Document record is only created after successful processing.
        """
        # Import here to avoid circular dependency
        from src.chat.service import chat_service

        # Create a user message for the uploaded document
        attachment_data = {
            "filename": filename,
            "file_type": file_type,
            "file_size": file_size,
            "status": "processing"
        }

        await chat_service.add_message(
            session_id=session_id,
            role="user",
            content=f"📎 Uploaded document: {filename}",
            attachments=[attachment_data]
        )

        # Send WebSocket notification about the new document (processing)
        await chat_ws_manager.send_to_user(str(user_id), {
            "type": "session_document_uploaded",
            "session_id": str(session_id),
            "document": {
                "filename": filename,
                "file_type": file_type,
                "file_size": file_size,
                "embedding_status": "processing"
            }
        })

        # Check if file type supports embeddings
        file_type_lower = file_type.lower() if file_type else ""
        supported_types = settings.SUPPORTED_EMBEDDING_TYPES

        if file_type_lower in supported_types:
            logger.info(f"Scheduling background embedding for document {filename}")
            # Use FastAPI BackgroundTasks if provided (more reliable than asyncio.create_task)
            if background_tasks:
                background_tasks.add_task(
                    SessionDocumentService._process_document_background_sync,
                    org_id=org_id,
                    s3_key=s3_key,
                    file_type=file_type_lower,
                    user_id=str(user_id),
                    session_id=str(session_id),
                    filename=filename,
                    file_size=file_size,
                    mime_type=mime_type
                )
            else:
                # Fallback to asyncio.create_task (less reliable but works for WebSocket calls)
                asyncio.create_task(
                    SessionDocumentService._process_document_background(
                        org_id=org_id,
                        s3_key=s3_key,
                        file_type=file_type_lower,
                        user_id=str(user_id),
                        session_id=str(session_id),
                        filename=filename,
                        file_size=file_size,
                        mime_type=mime_type
                    )
                )
        else:
            # File type doesn't support embeddings - still create document record
            session_document_id = uuid4()
            session_doc_data = {
                "id": str(session_document_id),
                "session_id": str(session_id),
                "user_id": str(user_id),
                "filename": filename,
                "file_type": file_type,
                "file_size": file_size,
                "s3_key": s3_key,
                "mime_type": mime_type,
                "embedding_status": "completed",
                "processed_at": datetime.utcnow().isoformat()
            }
            db.admin.table("session_documents").insert(session_doc_data).execute()

            # Update the chat message attachment with session_document_id and completed status
            await SessionDocumentService._update_message_attachment(
                session_id=str(session_id),
                filename=filename,
                session_document_id=str(session_document_id),
                status="completed"
            )

            # Send completion notification
            await chat_ws_manager.send_to_user(str(user_id), {
                "type": "session_document_ready",
                "session_id": str(session_id),
                "document_id": str(session_document_id),
                "filename": filename,
                "embedding_status": "completed"
            })

        # Return upload info
        return {
            "session_id": str(session_id),
            "filename": filename,
            "file_type": file_type,
            "file_size": file_size,
            "status": "processing"
        }

