"""Composed RAG service."""

from .rag_retrieval import RAGRetrievalMixin
from .rag_generation import RAGGenerationMixin
from .rag_nonstream import RAGNonStreamingMixin


class RAGService(RAGRetrievalMixin, RAGGenerationMixin, RAGNonStreamingMixin):
    """Service for retrieval-augmented generation workflows."""


from . import rag_retrieval as _rag_retrieval
from . import rag_generation as _rag_generation
from . import rag_nonstream as _rag_nonstream

_rag_retrieval.RAGService = RAGService
_rag_generation.RAGService = RAGService
_rag_nonstream.RAGService = RAGService

rag_service = RAGService()
