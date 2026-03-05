"""Session document service for chat document management."""
import asyncio
import logging
from typing import Optional, List, TYPE_CHECKING
from uuid import UUID, uuid4
from datetime import datetime

from src.core.database import db
from src.core.s3 import s3_client
from src.core.chat_websocket import chat_ws_manager
from src.services.embedding_service import embedding_service
from src.config import settings

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)


class SessionDocumentService:
    """Service for managing documents uploaded within chat sessions."""

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

    @staticmethod
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
        from src.services.chat_service import chat_service

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
        from src.core.pinecone_client import pinecone_client

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
            await pinecone_client.delete_by_document(
                document_id=str(session_document_id),
                namespace=str(user_id),
                index_name=settings.PINECONE_CHAT_SESSIONS_INDEX
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
        from src.core.pinecone_client import pinecone_client

        logger.info(f"Reprocessing embeddings for session document {session_document_id}")

        try:
            # Delete existing embeddings first
            await pinecone_client.delete_by_document(
                document_id=str(session_document_id),
                namespace=user_id,
                index_name=settings.PINECONE_CHAT_SESSIONS_INDEX
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

        # Use FastAPI BackgroundTasks if provided
        if background_tasks:
            background_tasks.add_task(
                SessionDocumentService._reprocess_document_background_sync,
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

    @staticmethod
    async def delete_all_session_documents(
        session_id: UUID,
        user_id: UUID
    ) -> bool:
        """
        Delete all documents associated with a session.
        Called when a session is deleted.
        """
        from src.core.pinecone_client import pinecone_client

        # Get all session documents
        session_docs = await SessionDocumentService.get_session_documents(session_id)

        # Delete each document's embeddings and S3 files
        for doc in session_docs:
            session_doc_id = doc.get("id")
            if session_doc_id:
                # Delete embeddings from chat-sessions index
                await pinecone_client.delete_by_document(
                    document_id=session_doc_id,
                    namespace=str(user_id),
                    index_name=settings.PINECONE_CHAT_SESSIONS_INDEX
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


session_document_service = SessionDocumentService()
