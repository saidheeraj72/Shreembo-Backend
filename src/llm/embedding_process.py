"""Embedding document processing pipeline."""
import logging
from typing import Optional
from uuid import UUID

from src.config import settings
from src.core.database import db
from src.core.openai_client import openai_client
from src.core.qdrant_client import qdrant_client
from src.core.websocket import ws_manager
from src.llm import sparse

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
        document_name: Optional[str] = None,
        folder_id: Optional[str] = None,
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
            chunks = await EmbeddingService.chunk_text_async(text)

            # Build embedding input: "filename > section" breadcrumb + the
            # overlap-prefixed body. The document name is a strong retrieval
            # signal on its own ("the Q3 report", "the NDA").
            # chunk.text stays clean — that is what gets stored and cited.
            embed_texts = []
            for chunk in chunks:
                breadcrumb = " > ".join(p for p in (document_name, chunk.section_header) if p)
                body = chunk.text_to_embed
                embed_texts.append(f"{breadcrumb}\n\n{body}" if breadcrumb else body)

            async def _embed_progress(done: int, total: int):
                if total > 1:
                    await ws_manager.send_upload_progress(
                        user_id, upload_id, "generating_embeddings",
                        60 + int(20 * done / total),
                    )

            embeddings = await openai_client.get_embeddings_batch(
                embed_texts, progress_cb=_embed_progress
            )
            await ws_manager.send_upload_progress(user_id, upload_id, "storing_embeddings", 80)

            if is_session_document:
                index_name = settings.QDRANT_SESSIONS_COLLECTION
                namespace = user_id
            else:
                index_name = None
                namespace = str(org_id) if org_id else user_id

            vectors = []
            for chunk, embedding, embed_text in zip(chunks, embeddings, embed_texts):
                metadata = {
                    "document_id": str(document_id),
                    "user_id": user_id,
                    "chunk_index": chunk.chunk_index,
                    "chunk_text": chunk.text,
                    "section_header": chunk.section_header,
                    "page_numbers": chunk.page_numbers,
                    "chunk_type": chunk.chunk_type,
                    # Enables permission pre-filtering at search time
                    "folder_id": str(folder_id) if folder_id else None,
                }
                if is_session_document and session_id:
                    metadata["session_id"] = session_id
                vectors.append({
                    "id": f"{document_id}_{chunk.chunk_index}",
                    "values": embedding,
                    "metadata": metadata,
                    # Lexical vector for hybrid search — same text as the dense
                    # side, so filename and section terms are matchable too.
                    "sparse": sparse.encode_document(embed_text).as_dict(),
                })

            # upsert() reports failure by returning False rather than raising, so
            # the result has to be checked: ignoring it marks a document "Ready"
            # with zero — or worse, partially — indexed chunks, and searches then
            # come back empty for a file the UI says is available. Roll the
            # partial write back, because search filters on the storage node's
            # status, not on embedding_status.
            for i in range(0, len(vectors), 50):
                if not await qdrant_client.upsert(vectors[i : i + 50], namespace, index_name):
                    await qdrant_client.delete_by_document(
                        str(document_id), namespace, index_name
                    )
                    raise RuntimeError(
                        f"Vector store rejected chunks {i}–{i + len(vectors[i : i + 50])} "
                        f"of {len(vectors)} for document {document_id}"
                    )

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
