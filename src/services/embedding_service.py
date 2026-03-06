"""Embedding service for document processing."""
import re
import tempfile
import os
import logging
from typing import Optional, List, Dict, Any
from uuid import UUID

from markitdown import MarkItDown

from src.core.s3 import s3_client
from src.core.openai_client import openai_client
from src.core.pinecone_client import pinecone_client
from src.core.websocket import ws_manager
from src.core.database import db
from src.config import settings
from src.utils.text_utils import sanitize_text

logger = logging.getLogger(__name__)


class RecursiveHeaderChunker:
    """
    Splits markdown by headers (Level 1-3) to preserve logical structure.
    If a section is too large, it sub-splits by paragraphs.
    """

    def __init__(self, max_chunk_size=800, overlap=100):
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap

    def split_text(self, text: str) -> List[Dict[str, Any]]:
        header_pattern = r'(^#{1,3}\s+.*)'
        parts = re.split(header_pattern, text, flags=re.MULTILINE)

        chunks = []
        current_header = "General"

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if re.match(r'^#{1,3}\s+', part):
                current_header = part
            else:
                if len(part) > self.max_chunk_size:
                    sub_chunks = self._sub_split_paragraph(part)
                    for sub in sub_chunks:
                        chunks.append({
                            "text": f"{current_header}\n\n{sub}",
                            "metadata": {"header": current_header}
                        })
                else:
                    chunks.append({
                        "text": f"{current_header}\n\n{part}",
                        "metadata": {"header": current_header}
                    })
        return chunks

    def _sub_split_paragraph(self, text: str) -> List[str]:
        """Split by double newline (paragraphs) and combine until max_chunk_size."""
        paragraphs = text.split("\n\n")
        current_chunk = []
        current_len = 0
        final_chunks = []

        for p in paragraphs:
            if current_len + len(p) > self.max_chunk_size:
                final_chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_len = len(p)
            else:
                current_chunk.append(p)
                current_len += len(p)

        if current_chunk:
            final_chunks.append("\n\n".join(current_chunk))
        return final_chunks


class EmbeddingService:
    """Service for document processing and embedding generation."""

    md_parser = MarkItDown()

    # --- Text Extraction ---

    @staticmethod
    def _extract_pdf_with_pymupdf(file_bytes: bytes) -> Optional[str]:
        """Extract text from PDF using PyMuPDF for better accuracy."""
        try:
            import pymupdf
            doc = pymupdf.open(stream=file_bytes, filetype="pdf")
            pages = [page.get_text() for page in doc]
            doc.close()
            text = "\n\n".join(pages)
            return text if text.strip() else None
        except Exception as e:
            logger.warning("PyMuPDF extraction failed: %s", e)
            return None

    @staticmethod
    def _extract_with_markitdown(file_bytes: bytes, file_type: str) -> Optional[str]:
        """Extract text using MarkItDown (supports PDF, DOCX, XLSX, PPTX, etc.)."""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_type}') as tmp_file:
                tmp_file.write(file_bytes)
                tmp_path = tmp_file.name
            try:
                result = EmbeddingService.md_parser.convert(tmp_path)
                return result.text_content
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        except Exception as e:
            logger.error("MarkItDown extraction error: %s", e)
            return None

    @staticmethod
    async def extract_text(s3_key: str, file_type: str) -> Optional[str]:
        """
        Extract text from a document.
        Uses PyMuPDF for PDFs (better accuracy), falls back to MarkItDown.
        Uses MarkItDown directly for all other file types.
        """
        content = await s3_client.get_file_content(s3_key)
        if not content:
            return None

        text = None
        if file_type == "pdf":
            text = EmbeddingService._extract_pdf_with_pymupdf(content)
            if text:
                return sanitize_text(text)
            logger.info("PyMuPDF returned no text, falling back to MarkItDown for PDF")

        text = EmbeddingService._extract_with_markitdown(content, file_type)
        return sanitize_text(text) if text else None

    # --- Chunking ---

    @staticmethod
    def chunk_text(text: str) -> List[str]:
        """Split text into chunks using RecursiveHeaderChunker to preserve document structure."""
        chunker = RecursiveHeaderChunker(max_chunk_size=1500, overlap=100)
        chunks_with_metadata = chunker.split_text(text)
        return [chunk['text'] for chunk in chunks_with_metadata]

    # --- Document Processing ---

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
        """Process document: extract text, chunk, generate embeddings, store in Pinecone."""
        try:
            if not is_session_document:
                db.admin.table("storage_nodes").update({
                    "processing_status": "processing"
                }).eq("id", str(document_id)).execute()

            await ws_manager.send_upload_progress(user_id, upload_id, "extracting", 40)

            # Skip if embeddings disabled or unsupported file type
            if not settings.ENABLE_EMBEDDINGS or file_type not in settings.SUPPORTED_EMBEDDING_TYPES:
                if not is_session_document:
                    db.admin.table("storage_nodes").update({
                        "processing_status": "completed",
                        "embedding_status": "skipped"
                    }).eq("id", str(document_id)).execute()
                await ws_manager.send_upload_progress(user_id, upload_id, "complete", 100, str(document_id))
                return

            # Extract text
            text = await EmbeddingService.extract_text(s3_key, file_type)
            if not text:
                if not is_session_document:
                    db.admin.table("storage_nodes").update({
                        "processing_status": "completed",
                        "embedding_status": "failed"
                    }).eq("id", str(document_id)).execute()
                await ws_manager.send_upload_progress(user_id, upload_id, "complete", 100, str(document_id))

                if is_session_document:
                    raise ValueError("Text extraction failed")
                return

            await ws_manager.send_upload_progress(user_id, upload_id, "generating_embeddings", 60)

            # Chunk and embed
            chunks = EmbeddingService.chunk_text(text)
            embeddings = await openai_client.get_embeddings_batch(chunks)

            await ws_manager.send_upload_progress(user_id, upload_id, "storing_embeddings", 80)

            # Determine Pinecone index and namespace
            if is_session_document:
                index_name = settings.PINECONE_CHAT_SESSIONS_INDEX
                pinecone_namespace = user_id
            else:
                index_name = None
                pinecone_namespace = str(org_id) if org_id else user_id

            # Build vectors
            vectors = []
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                metadata = {
                    'document_id': str(document_id),
                    'user_id': user_id,
                    'chunk_index': i,
                    'chunk_text': chunk,
                }
                if is_session_document and session_id:
                    metadata['session_id'] = session_id

                vectors.append({
                    'id': f"{document_id}_{i}",
                    'values': embedding,
                    'metadata': metadata
                })

            # Upsert in batches (Pinecone 2MB limit per request)
            batch_size = 50
            for i in range(0, len(vectors), batch_size):
                await pinecone_client.upsert(vectors[i:i + batch_size], pinecone_namespace, index_name)

            if not is_session_document:
                db.admin.table("storage_nodes").update({
                    "processing_status": "completed",
                    "embedding_status": "completed"
                }).eq("id", str(document_id)).execute()

            await ws_manager.send_upload_progress(user_id, upload_id, "complete", 100, str(document_id))

        except Exception as e:
            logger.error("Embedding error: %s", e)
            if not is_session_document:
                db.admin.table("storage_nodes").update({
                    "processing_status": "failed",
                    "embedding_status": "failed"
                }).eq("id", str(document_id)).execute()
            await ws_manager.send_upload_progress(user_id, upload_id, "failed", 0, error=str(e))
            raise

    # --- Search ---

    @staticmethod
    async def search(
        query: str,
        org_id: Optional[UUID],
        user_id: UUID,
        top_k: int = 10,
        folder_id: Optional[UUID] = None,
    ) -> List[dict]:
        """Search documents using vector similarity."""
        query_embedding = await openai_client.get_embedding(query)

        filter_dict = {'folder_id': str(folder_id)} if folder_id else None
        pinecone_namespace = str(org_id) if org_id else str(user_id)

        results = await pinecone_client.query(query_embedding, pinecone_namespace, top_k, filter_dict)

        doc_ids = list(set(r.metadata.get('document_id') for r in results if r.metadata))
        if not doc_ids:
            return []

        docs = db.admin.table("storage_nodes").select("*").in_("id", doc_ids).execute()
        doc_map = {d['id']: d for d in docs.data}

        return [
            {
                'score': r.score,
                'document': doc_map.get(r.metadata['document_id']),
                'chunk_text': r.metadata.get('chunk_text', '')
            }
            for r in results if r.metadata.get('document_id') in doc_map
        ]


embedding_service = EmbeddingService()
