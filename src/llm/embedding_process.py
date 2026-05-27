"""Embedding document processing pipeline."""
import logging
from typing import Optional
from uuid import UUID

from src.config import settings
from src.core.database import db
from src.core.openai_client import openai_client
from src.core.qdrant_client import qdrant_client
from src.core.websocket import ws_manager

logger = logging.getLogger(__name__)


class EmbeddingProcessMixin:
    @staticmethod
    async def process_document(
        document_id: UUID,
        org_id: Optional[UUID],
        s3_key: str,
        file_type: str,
        user_id: str,
        upload_id: str,
        is_session_document: bool = False,
        session_id: Optional[str] = None,
    ):
        try:
            if not is_session_document:
                db.admin.table("storage_nodes").update({"processing_status": "processing"}).eq(
                    "id", str(document_id)
                ).execute()

            await ws_manager.send_upload_progress(user_id, upload_id, "extracting", 40)

            if not settings.ENABLE_EMBEDDINGS or file_type not in settings.SUPPORTED_EMBEDDING_TYPES:
                if not is_session_document:
                    db.admin.table("storage_nodes").update(
                        {"processing_status": "completed", "embedding_status": "skipped"}
                    ).eq("id", str(document_id)).execute()
                await ws_manager.send_upload_progress(user_id, upload_id, "complete", 100, str(document_id))
                return

            text = await EmbeddingService.extract_text(s3_key, file_type)
            if not text:
                if not is_session_document:
                    db.admin.table("storage_nodes").update(
                        {"processing_status": "completed", "embedding_status": "failed"}
                    ).eq("id", str(document_id)).execute()
                await ws_manager.send_upload_progress(user_id, upload_id, "complete", 100, str(document_id))
                if is_session_document:
                    raise ValueError("Text extraction failed")
                return

            await ws_manager.send_upload_progress(user_id, upload_id, "generating_embeddings", 60)
            chunks = EmbeddingService.chunk_text(text)

            # Build embedding input: prepend section header for better retrieval
            embed_texts = []
            for chunk in chunks:
                if chunk.section_header:
                    embed_texts.append(f"{chunk.section_header}\n\n{chunk.text}")
                else:
                    embed_texts.append(chunk.text)

            embeddings = await openai_client.get_embeddings_batch(embed_texts)
            await ws_manager.send_upload_progress(user_id, upload_id, "storing_embeddings", 80)

            if is_session_document:
                index_name = settings.QDRANT_SESSIONS_COLLECTION
                namespace = user_id
            else:
                index_name = None
                namespace = str(org_id) if org_id else user_id

            vectors = []
            for chunk, embedding in zip(chunks, embeddings):
                metadata = {
                    "document_id": str(document_id),
                    "user_id": user_id,
                    "chunk_index": chunk.chunk_index,
                    "chunk_text": chunk.text,
                    "section_header": chunk.section_header,
                    "page_numbers": chunk.page_numbers,
                    "chunk_type": chunk.chunk_type,
                }
                if is_session_document and session_id:
                    metadata["session_id"] = session_id
                vectors.append({"id": f"{document_id}_{chunk.chunk_index}", "values": embedding, "metadata": metadata})

            for i in range(0, len(vectors), 50):
                await qdrant_client.upsert(vectors[i : i + 50], namespace, index_name)

            if not is_session_document:
                db.admin.table("storage_nodes").update(
                    {"processing_status": "completed", "embedding_status": "completed"}
                ).eq("id", str(document_id)).execute()

            await ws_manager.send_upload_progress(user_id, upload_id, "complete", 100, str(document_id))
        except Exception as e:
            logger.error("Embedding error: %s", e)
            if not is_session_document:
                db.admin.table("storage_nodes").update(
                    {"processing_status": "failed", "embedding_status": "failed"}
                ).eq("id", str(document_id)).execute()
            await ws_manager.send_upload_progress(user_id, upload_id, "failed", 0, error=str(e))
            raise
