"""Composed embedding service."""

from .embedding_core import SmartDocumentChunker, Chunk, EmbeddingCoreMixin
from .embedding_process import EmbeddingProcessMixin
from .embedding_search import EmbeddingSearchMixin


class EmbeddingService(EmbeddingCoreMixin, EmbeddingProcessMixin, EmbeddingSearchMixin):
    """Service for extraction, chunking, embedding, and semantic search."""


from . import embedding_core as _embedding_core
from . import embedding_process as _embedding_process

_embedding_core.EmbeddingService = EmbeddingService
_embedding_process.EmbeddingService = EmbeddingService

embedding_service = EmbeddingService()
