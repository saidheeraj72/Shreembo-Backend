"""Embedding service for document processing."""
import hashlib
import re
import tempfile
import os
from typing import Optional, List, Dict, Any
from uuid import UUID

from markitdown import MarkItDown

from src.core.s3 import s3_client
from src.core.openai_client import openai_client
from src.core.pinecone_client import pinecone_client
from src.core.websocket import ws_manager
from src.core.database import db
from src.config import settings


class RecursiveHeaderChunker:
    """
    Splits markdown by headers (Level 1-3) to preserve logical structure.
    If a section is too large, it sub-splits by paragraphs.
    """
    def __init__(self, max_chunk_size=800, overlap=100):
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap

    def split_text(self, text: str) -> List[Dict[str, Any]]:
        # Regex to match headers like "# Title" or "### Section"
        # We use a capture group to keep the delimiter for reconstruction
        # This splits the text into [content, header, content, header...]
        header_pattern = r'(^#{1,3}\s+.*)'
        parts = re.split(header_pattern, text, flags=re.MULTILINE)

        chunks = []
        current_header = "General"

        # 'parts' will look like: ['', '# Header 1', 'Content...', '## Header 2', 'Content...']
        # We iterate and group them.
        for part in parts:
            part = part.strip()
            if not part:
                continue

            # If it's a header, update current context
            if re.match(r'^#{1,3}\s+', part):
                current_header = part
            else:
                # It's content. If too big, sub-split.
                if len(part) > self.max_chunk_size:
                    sub_chunks = self._sub_split_paragraph(part)
                    for sub in sub_chunks:
                        chunks.append({
                            "text": f"{current_header}\n\n{sub}", # Inject context
                            "metadata": {"header": current_header}
                        })
                else:
                    chunks.append({
                        "text": f"{current_header}\n\n{part}",
                        "metadata": {"header": current_header}
                    })
        return chunks

    def _sub_split_paragraph(self, text: str) -> List[str]:
        """Simple fallback: split by double newline (paragraphs)"""
        paragraphs = text.split("\n\n")
        # Combine paragraphs until max_chunk_size is reached (simple logic)
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
    """Service for document processing with MarkItDown parser and RecursiveHeaderChunker."""

    # Initialize MarkItDown parser
    md_parser = MarkItDown()

    @staticmethod
    async def extract_text(s3_key: str, file_type: str) -> Optional[str]:
        """Extract text from document using MarkItDown parser."""
        content = await s3_client.get_file_content(s3_key)
        if not content:
            return None

        try:
            # Save content to temporary file for MarkItDown processing
            with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{file_type}') as tmp_file:
                tmp_file.write(content)
                tmp_path = tmp_file.name

            try:
                # Use MarkItDown to convert file to markdown
                result = EmbeddingService.md_parser.convert(tmp_path)
                return result.text_content
            finally:
                # Clean up temp file
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            print(f"Text extraction error with MarkItDown: {e}")
            return None

    @staticmethod
    def chunk_text(text: str) -> List[str]:
        """Split text into chunks using RecursiveHeaderChunker to preserve document structure."""
        chunker = RecursiveHeaderChunker(max_chunk_size=1500, overlap=100)
        chunks_with_metadata = chunker.split_text(text)

        # Return just the text parts for backward compatibility
        return [chunk['text'] for chunk in chunks_with_metadata]

    @staticmethod
    async def process_document(document_id: UUID, org_id: Optional[UUID], s3_key: str,
                               file_type: str, user_id: str, upload_id: str):
        """Process document: extract text and generate embeddings."""
        try:
            # Update status
            db.admin.table("storage_nodes").update({
                "processing_status": "processing"
            }).eq("id", str(document_id)).execute()

            await ws_manager.send_upload_progress(user_id, upload_id, "extracting", 40)

            # Check if embeddings are enabled and file type is supported
            if not settings.ENABLE_EMBEDDINGS or file_type not in settings.SUPPORTED_EMBEDDING_TYPES:
                db.admin.table("storage_nodes").update({
                    "processing_status": "completed",
                    "embedding_status": "skipped"
                }).eq("id", str(document_id)).execute()
                await ws_manager.send_upload_progress(user_id, upload_id, "complete", 100, str(document_id))
                return

            # Extract text
            text = await EmbeddingService.extract_text(s3_key, file_type)
            if not text:
                db.admin.table("storage_nodes").update({
                    "processing_status": "completed",
                    "embedding_status": "failed"
                }).eq("id", str(document_id)).execute()
                await ws_manager.send_upload_progress(user_id, upload_id, "complete", 100, str(document_id))
                return

            await ws_manager.send_upload_progress(user_id, upload_id, "generating_embeddings", 60)

            # Chunk text
            chunks = EmbeddingService.chunk_text(text)

            # Generate embeddings
            embeddings = await openai_client.get_embeddings_batch(chunks)

            await ws_manager.send_upload_progress(user_id, upload_id, "storing_embeddings", 80)

            pinecone_namespace = str(org_id) if org_id else user_id # Consistent namespace
            # Prepare vectors for Pinecone
            vectors = []
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                vectors.append({
                    'id': f"{document_id}_{i}",
                    'values': embedding,
                    'metadata': {
                        'document_id': str(document_id),
                        'org_id': pinecone_namespace, # Consistent identifier
                        'chunk_index': i,
                        'chunk_text': chunk,  # Store full chunk text
                    }
                })

            # Store in Pinecone
            await pinecone_client.upsert(vectors, pinecone_namespace)

            # Update document status
            db.admin.table("storage_nodes").update({
                "processing_status": "completed",
                "embedding_status": "completed"
            }).eq("id", str(document_id)).execute()

            await ws_manager.send_upload_progress(user_id, upload_id, "complete", 100, str(document_id))

        except Exception as e:
            print(f"Embedding error: {e}")
            db.admin.table("storage_nodes").update({
                "processing_status": "failed",
                "embedding_status": "failed"
            }).eq("id", str(document_id)).execute()
            await ws_manager.send_upload_progress(user_id, upload_id, "failed", 0, error=str(e))

    @staticmethod
    async def search(query: str, org_id: Optional[UUID], user_id: UUID, top_k: int = 10,
                     folder_id: Optional[UUID] = None) -> List[dict]:
        """Search documents using vector similarity."""
        # Get query embedding
        query_embedding = await openai_client.get_embedding(query)

        # Build filter
        filter_dict = None
        if folder_id:
            filter_dict = {'folder_id': str(folder_id)}

        pinecone_namespace = str(org_id) if org_id else str(user_id) # Consistent namespace
        # Query Pinecone
        results = await pinecone_client.query(query_embedding, pinecone_namespace, top_k, filter_dict)

        # Get document details
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
