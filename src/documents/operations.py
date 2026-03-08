"""Auto-split document service part."""
from typing import Optional, List
from uuid import UUID, uuid4
from urllib.parse import quote
import zipfile
from io import BytesIO

from src.core.database import db
from src.core.s3 import s3_client
from src.core.exceptions import NotFoundError, ValidationError, ConflictError
from src.llm.embedding import embedding_service


class DocumentOperationsMixin:
    @staticmethod
    async def get_document(doc_id: UUID, org_id: Optional[UUID]) -> Optional[dict]:
        query = db.admin.table("storage_nodes").select("*").eq("id", str(doc_id))

        if org_id:
            query = query.eq("org_id", str(org_id))
        else:
            query = query.is_("org_id", "null")

        try:
            result = query.maybe_single().execute()
            if result and result.data:
                return result.data
        except Exception as e:
            # 406 Not Acceptable is returned by PostgREST when maybe_single finds 0 rows
            # 204 Missing response might also occur
            # This is expected for "Not Found"
            error_str = str(e)
            if "406" not in error_str and "'code': '204'" not in error_str:
                logger.error("Error fetching document with org filter: %s", e)
            # Fallback: try without org_id filter
            pass

        # Fallback: Get document without org_id filter
        try:
            result = db.admin.table("storage_nodes").select("*").eq(
                "id", str(doc_id)
            ).eq("status", "active").maybe_single().execute()
            return result.data if result and result.data else None
        except Exception as e:
            error_str = str(e)
            if "406" not in error_str and "'code': '204'" not in error_str:
                logger.error("Error fetching document: %s", e)
            return None

    @staticmethod
    async def update_document(doc_id: UUID, org_id: Optional[UUID], **updates) -> Optional[dict]:
        updates["updated_at"] = datetime.utcnow().isoformat()
        query = db.admin.table("storage_nodes").update(updates).eq("id", str(doc_id))
        
        if org_id:
            query = query.eq("org_id", str(org_id))
        else:
            query = query.is_("org_id", "null")
            
        result = query.execute()
        return result.data[0] if result.data else None

    @staticmethod
    async def delete_document(doc_id: UUID, org_id: Optional[UUID]) -> bool:
        doc = await DocumentService.get_document(doc_id, org_id)
        if not doc:
            return False

        # Delete from S3
        if doc.get("s3_key"):
            await s3_client.delete_file(doc["s3_key"])

        # Delete from vector DB
        from src.core.qdrant_client import qdrant_client
        await qdrant_client.delete_by_document(str(doc_id), str(org_id) if org_id else "personal")

        # Soft delete in DB
        db.admin.table("storage_nodes").update({
            "status": "deleted",
            "deleted_at": datetime.utcnow().isoformat()
        }).eq("id", str(doc_id)).execute()

        return True

    @staticmethod
    async def move_document(doc_id: UUID, org_id: Optional[UUID], target_folder_id: Optional[UUID]) -> Optional[dict]:
        return await DocumentService.update_document(
            doc_id, org_id,
            parent_id=str(target_folder_id) if target_folder_id else None
        )

    @staticmethod
    async def get_download_url(doc_id: UUID, org_id: Optional[UUID]) -> Optional[str]:
        doc = await DocumentService.get_document(doc_id, org_id)
        
        # Fallback: Check session_documents if not found in storage_nodes
        if not doc:
            try:
                # session_documents does not have org_id column
                result = db.admin.table("session_documents").select("*").eq("id", str(doc_id)).maybe_single().execute()
                
                if result and result.data:
                    doc = result.data
                    doc["name"] = doc.get("filename")
            except Exception:
                pass

        if not doc or not doc.get("s3_key"):
            return None
        return await s3_client.get_presigned_download_url(doc["s3_key"], doc.get("name"))

    @staticmethod
    async def get_view_url(doc_id: UUID, org_id: Optional[UUID]) -> Optional[str]:
        """Get presigned URL for viewing document in browser."""
        doc = await DocumentService.get_document(doc_id, org_id)
        
        # Fallback: Check session_documents if not found in storage_nodes
        if not doc:
            try:
                # session_documents does not have org_id column
                result = db.admin.table("session_documents").select("*").eq("id", str(doc_id)).maybe_single().execute()
                
                if result and result.data:
                    doc = result.data
                    doc["name"] = doc.get("filename") # Ensure name is available for view as well
                    # Use mime_type if available, else fallback to file_type (though file_type is usually extension)
                    if not doc.get("mime_type") and doc.get("file_type"):
                        doc["mime_type"] = doc.get("file_type")
            except Exception:
                pass

        if not doc or not doc.get("s3_key"):
            return None
        return await s3_client.get_presigned_view_url(doc["s3_key"], doc.get("mime_type"))

