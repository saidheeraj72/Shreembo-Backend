"""Session document service for chat document management."""
import asyncio
from typing import Optional, List
from uuid import UUID, uuid4
from datetime import datetime

from src.core.database import db
from src.core.s3 import s3_client
from src.services.embedding_service import embedding_service
from src.config import settings


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

        Creates session_document record only (not in storage_nodes),
        returns presigned URL for S3 upload.
        """
        # Generate S3 key
        s3_key = SessionDocumentService.generate_session_s3_key(
            user_id, session_id, filename
        )

        # Get presigned upload URL
        presigned = await s3_client.get_presigned_upload_url(s3_key, content_type)

        # Extract file extension
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else None

        # Create session_document entry (session-specific, not in storage_nodes)
        session_doc_data = {
            "session_id": str(session_id),
            "user_id": str(user_id),
            "filename": filename,
            "file_type": ext,
            "file_size": size_bytes,
            "s3_key": s3_key,
            "mime_type": content_type,
            "embedding_status": "pending"
        }
        session_doc_result = db.admin.table("session_documents").insert(
            session_doc_data
        ).execute()

        session_doc = session_doc_result.data[0]

        return {
            "upload_id": str(uuid4()),
            "upload_url": presigned["upload_url"],
            "s3_key": s3_key,
            "session_document_id": session_doc["id"]
        }

    @staticmethod
    async def _process_document_background(
        session_document_id: UUID,
        org_id: Optional[UUID],
        s3_key: str,
        file_type: str,
        user_id: str,
        session_id: str
    ):
        """
        Background task for processing document embeddings.
        This runs asynchronously without blocking the HTTP response.
        """
        try:
            # Process embeddings for session document (uses chat-sessions index)
            # Use session_document_id as the document_id since we're not using storage_nodes
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

        except Exception as e:
            print(f"Background processing failed for {session_document_id}: {e}")
            # Update failed status
            db.admin.table("session_documents").update({
                "embedding_status": "failed"
            }).eq("id", str(session_document_id)).execute()

    @staticmethod
    async def complete_upload(
        session_document_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID]
    ) -> dict:
        """
        Complete document upload and trigger embedding in background.

        Returns immediately - processing continues via WebSocket progress updates.
        """
        # Import here to avoid circular dependency
        from src.services.chat_service import chat_service

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

        # Create a system message for the uploaded document
        attachment_data = {
            "session_document_id": str(session_document_id),
            "filename": session_doc["filename"],
            "file_type": session_doc.get("file_type"),
            "file_size": session_doc.get("file_size", 0)
        }

        await chat_service.add_message(
            session_id=UUID(session_doc["session_id"]),
            role="system",
            content=f"📎 Uploaded document: {session_doc['filename']}",
            attachments=[attachment_data]
        )

        # Update status to processing
        db.admin.table("session_documents").update({
            "embedding_status": "processing"
        }).eq("id", str(session_document_id)).execute()

        # Check if file type supports embeddings
        file_type = session_doc.get("file_type", "").lower()
        supported_types = settings.SUPPORTED_EMBEDDING_TYPES

        if file_type in supported_types:
            # Start background processing (non-blocking)
            asyncio.create_task(
                SessionDocumentService._process_document_background(
                    session_document_id=session_document_id,
                    org_id=org_id,
                    s3_key=session_doc["s3_key"],
                    file_type=file_type,
                    user_id=str(user_id),
                    session_id=session_doc["session_id"]
                )
            )
        else:
            # File type doesn't support embeddings
            db.admin.table("session_documents").update({
                "embedding_status": "completed",
                "processed_at": datetime.utcnow().isoformat()
            }).eq("id", str(session_document_id)).execute()

        # Return immediately - client will receive progress via WebSocket
        return session_doc

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
                    print(f"Failed to delete S3 file {s3_key}: {e}")

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
                        print(f"Failed to delete S3 file {s3_key}: {e}")

        # Delete all session_documents entries
        result = db.admin.table("session_documents").delete().eq(
            "session_id", str(session_id)
        ).execute()

        return True


session_document_service = SessionDocumentService()
