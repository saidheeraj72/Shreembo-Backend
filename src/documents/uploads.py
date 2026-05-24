"""Auto-split document service part."""
import asyncio
from typing import Optional, List
from uuid import UUID, uuid4
from urllib.parse import quote
import zipfile
from io import BytesIO

from src.core.database import db
from src.core.s3 import s3_client
from src.core.exceptions import NotFoundError, ValidationError, ConflictError
from src.core.websocket import ws_manager
from src.llm.embedding import embedding_service


class DocumentUploadsMixin:
    @staticmethod
    async def direct_upload(org_id: Optional[UUID], owner_id: UUID, filename: str,
                            content_type: str, size_bytes: int, file_bytes: bytes,
                            parent_id: Optional[UUID] = None,
                            branch_id: Optional[UUID] = None,
                            description: str = None,
                            tags: List[str] = None) -> dict:
        """Upload file to Supabase Storage and create document record."""
        upload_id = str(uuid4())
        prefix = str(org_id) if org_id else str(owner_id)
        s3_key = s3_client.generate_key(prefix, filename)

        # Upload to Supabase Storage
        await s3_client.upload_file_bytes(file_bytes, s3_key, content_type)

        # Create document record
        ext = filename.rsplit(".", 1)[-1] if "." in filename else None
        data = {
            "org_id": str(org_id) if org_id else None,
            "owner_id": str(owner_id),
            "name": filename,
            "node_type": "file",
            "mime_type": content_type,
            "file_size": size_bytes,
            "file_extension": ext,
            "s3_key": s3_key,
            "s3_bucket": None,
            "parent_id": str(parent_id) if parent_id else None,
            "branch_id": str(branch_id) if branch_id else None,
            "description": description,
            "tags": tags,
            "processing_status": "pending",
            "embedding_status": "pending",
        }

        result = db.admin.table("storage_nodes").insert(data).execute()
        document = result.data[0] if result.data else None

        if document:
            await ws_manager.send_upload_progress(
                str(owner_id), upload_id, "processing", 30, document["id"]
            )

            asyncio.create_task(
                embedding_service.process_document(
                    document_id=UUID(document["id"]),
                    org_id=org_id,
                    s3_key=s3_key,
                    file_type=ext,
                    user_id=str(owner_id),
                    upload_id=upload_id
                )
            )

        return document
