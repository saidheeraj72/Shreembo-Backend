"""Session document service for chat document management."""
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

        Creates storage_node and session_document records,
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

        # Create storage_node entry (permanent document)
        doc_data = {
            "org_id": str(org_id) if org_id else None,
            "owner_id": str(user_id),
            "name": filename,
            "node_type": "file",
            "mime_type": content_type,
            "file_size": size_bytes,
            "file_extension": ext,
            "storage_path": s3_key,
            "status": "active",
        }
        doc_result = db.admin.table("storage_nodes").insert(doc_data).execute()
        document = doc_result.data[0]

        # Create session_document link
        session_doc_data = {
            "session_id": str(session_id),
            "document_id": document["id"],
            "user_id": str(user_id),
            "filename": filename,
            "file_type": ext,
            "file_size": size_bytes,
            "s3_key": s3_key,
            "embedding_status": "pending"
        }
        session_doc_result = db.admin.table("session_documents").insert(
            session_doc_data
        ).execute()

        return {
            "upload_id": str(uuid4()),
            "upload_url": presigned["upload_url"],
            "s3_key": s3_key,
            "document_id": document["id"],
            "session_document_id": session_doc_result.data[0]["id"]
        }

    @staticmethod
    async def complete_upload(
        session_document_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID]
    ) -> dict:
        """
        Complete document upload and trigger embedding.

        Called after client confirms S3 upload is done.
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

        # Update status to processing
        db.admin.table("session_documents").update({
            "embedding_status": "processing"
        }).eq("id", str(session_document_id)).execute()

        # Check if file type supports embeddings
        file_type = session_doc.get("file_type", "").lower()
        supported_types = settings.SUPPORTED_EMBEDDING_TYPES

        if file_type in supported_types:
            try:
                # Process embeddings
                await embedding_service.process_document(
                    document_id=UUID(session_doc["document_id"]),
                    org_id=org_id,
                    s3_key=session_doc["s3_key"],
                    file_type=file_type,
                    user_id=str(user_id),
                    upload_id=str(session_document_id)
                )

                # Update completion status
                db.admin.table("session_documents").update({
                    "embedding_status": "completed",
                    "processed_at": datetime.utcnow().isoformat()
                }).eq("id", str(session_document_id)).execute()

            except Exception as e:
                # Update failed status
                db.admin.table("session_documents").update({
                    "embedding_status": "failed"
                }).eq("id", str(session_document_id)).execute()
                raise e
        else:
            # File type doesn't support embeddings
            db.admin.table("session_documents").update({
                "embedding_status": "completed",
                "processed_at": datetime.utcnow().isoformat()
            }).eq("id", str(session_document_id)).execute()

        return session_doc

    @staticmethod
    async def get_session_documents(session_id: UUID) -> List[dict]:
        """Get all documents for a session."""
        result = db.admin.table("session_documents").select(
            "*, storage_nodes(id, name, mime_type, file_size)"
        ).eq(
            "session_id", str(session_id)
        ).order("uploaded_at").execute()

        return result.data if result.data else []

    @staticmethod
    async def get_session_document(session_document_id: UUID) -> Optional[dict]:
        """Get a single session document."""
        result = db.admin.table("session_documents").select(
            "*, storage_nodes(id, name, mime_type, file_size, storage_path)"
        ).eq("id", str(session_document_id)).single().execute()

        return result.data if result.data else None

    @staticmethod
    async def delete_session_document(
        session_document_id: UUID,
        user_id: UUID
    ) -> bool:
        """
        Delete a session document.

        Note: This doesn't delete the storage_node or S3 file,
        just removes the session association.
        """
        result = db.admin.table("session_documents").delete().eq(
            "id", str(session_document_id)
        ).eq("user_id", str(user_id)).execute()

        return bool(result.data)

    @staticmethod
    async def get_session_document_ids(session_id: UUID) -> List[str]:
        """Get document IDs for a session (for RAG filtering)."""
        result = db.admin.table("session_documents").select(
            "document_id"
        ).eq("session_id", str(session_id)).eq(
            "embedding_status", "completed"
        ).execute()

        return [d["document_id"] for d in result.data] if result.data else []


session_document_service = SessionDocumentService()
