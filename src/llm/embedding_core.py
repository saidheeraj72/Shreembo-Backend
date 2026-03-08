"""Embedding extraction and chunking helpers."""
import logging
import os
import tempfile
from typing import List, Optional

from markitdown import MarkItDown

from src.core.s3 import s3_client
from src.utils.text_utils import sanitize_text

logger = logging.getLogger(__name__)


class RecursiveHeaderChunker:
    """Split markdown text into heading-aware chunks."""

    def split_text(self, text: str, max_chunk_size: int = 1500) -> List[str]:
        lines = text.split("\n")
        chunks: List[str] = []
        current_chunk: List[str] = []

        for line in lines:
            if line.startswith("#") and current_chunk:
                chunk_text = "\n".join(current_chunk).strip()
                if chunk_text:
                    chunks.extend(self._split_if_too_large(chunk_text, max_chunk_size))
                current_chunk = [line]
            else:
                current_chunk.append(line)

        if current_chunk:
            chunk_text = "\n".join(current_chunk).strip()
            if chunk_text:
                chunks.extend(self._split_if_too_large(chunk_text, max_chunk_size))

        return chunks

    def _split_if_too_large(self, text: str, max_size: int) -> List[str]:
        if len(text) <= max_size:
            return [text]

        paragraphs = text.split("\n\n")
        chunks: List[str] = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 <= max_size:
                current = f"{current}\n\n{para}" if current else para
            else:
                if current:
                    chunks.append(current)
                current = para
        if current:
            chunks.append(current)
        return chunks


class EmbeddingCoreMixin:
    """Core extraction/chunking methods used by EmbeddingService."""

    md_parser = MarkItDown()

    @staticmethod
    def _extract_pdf_with_pymupdf(file_bytes: bytes) -> Optional[str]:
        try:
            import pymupdf
            import pymupdf4llm

            doc = pymupdf.open(stream=file_bytes, filetype="pdf")
            md_text = pymupdf4llm.to_markdown(doc)
            doc.close()
            return md_text if md_text.strip() else None
        except Exception as e:
            logger.warning("PyMuPDF markdown conversion failed: %s", e)
            return None

    @staticmethod
    def _extract_with_markitdown(file_bytes: bytes, file_type: str) -> Optional[str]:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}") as tmp_file:
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
        content = await s3_client.get_file_content(s3_key)
        if not content:
            return None

        text = (
            EmbeddingService._extract_pdf_with_pymupdf(content)
            if file_type == "pdf"
            else EmbeddingService._extract_with_markitdown(content, file_type)
        )
        return sanitize_text(text) if text else None

    @staticmethod
    def chunk_text(text: str) -> List[str]:
        chunker = RecursiveHeaderChunker()
        return chunker.split_text(text, max_chunk_size=1200)
