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


class SessionDocumentUploadMixin:
    @staticmethod
    def generate_session_s3_key(
        user_id: UUID,
        session_id: UUID,
        filename: str
    ) -> str:
        """Generate S3 key for session document."""
        unique_id = str(uuid4())[:8]
        # Sanitize filename
        safe_name = "".join(
            c if c.isalnum() or c in ".-_" else "_"
            for c in filename
        )
        return f"chat-sessions/{user_id}/{session_id}/{unique_id}_{safe_name}"

    @staticmethod
    async def _update_message_attachment(
        session_id: str,
        filename: str,
        session_document_id: str,
        status: str
    ):
        """Update a message attachment with session_document_id and status after processing."""
        try:
            # Find the message with this attachment
            messages_result = db.admin.table("chat_messages").select(
                "id", "attachments"
            ).eq("session_id", session_id).execute()

            for msg in messages_result.data or []:
                attachments = msg.get("attachments") or []
                updated = False

                for att in attachments:
                    if att.get("filename") == filename and att.get("status") == "processing":
                        att["session_document_id"] = session_document_id
                        att["status"] = status
                        updated = True
                        break

                if updated:
                    db.admin.table("chat_messages").update({
                        "attachments": attachments
                    }).eq("id", msg["id"]).execute()
                    break

        except Exception as e:
            logger.error(f"Failed to update message attachment: {e}")

    @staticmethod
    async def init_upload(
        session_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID],
        filename: str,
        content_type: str,
        size_bytes: int
    ) -> dict:
        """
        Initialize a document upload for a chat session.

        Returns presigned URL for S3 upload. Document record is created
        only after processing completes successfully.
        """
        # Generate S3 key
        s3_key = SessionDocumentService.generate_session_s3_key(
            user_id, session_id, filename
        )

        # Get presigned upload URL
        presigned = await s3_client.get_presigned_upload_url(s3_key, content_type)

        # Extract file extension
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else None

        # Return upload info - document will be created after processing
        return {
            "upload_id": str(uuid4()),
            "upload_url": presigned["upload_url"],
            "s3_key": s3_key,
            "session_id": str(session_id),
            "user_id": str(user_id),
            "filename": filename,
            "file_type": ext,
            "file_size": size_bytes,
            "mime_type": content_type
        }

    @staticmethod
    def _process_document_background_sync(
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
        Synchronous wrapper for background task processing.
        FastAPI BackgroundTasks works better with sync functions that run their own event loop.
        """
        asyncio.run(SessionDocumentService._process_document_background(
            org_id=org_id,
            s3_key=s3_key,
            file_type=file_type,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            file_size=file_size,
            mime_type=mime_type
        ))
