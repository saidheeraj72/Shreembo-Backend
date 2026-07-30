"""Composed embedding service."""

from .embedding_core import SmartDocumentChunker, Chunk, EmbeddingCoreMixin
from .embedding_process import EmbeddingProcessMixin
from .embedding_search import EmbeddingSearchMixin


class EmbeddingService(EmbeddingCoreMixin, EmbeddingProcessMixin, EmbeddingSearchMixin):
    """Service for extraction, chunking, embedding, and semantic search."""


embedding_service = EmbeddingService()
