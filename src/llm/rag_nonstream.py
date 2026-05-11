"""Auto-split RAG service part."""
from typing import Optional, List, Dict, Any, AsyncGenerator
from uuid import UUID
import logging

from src.core.database import db
from src.core.openai_client import openai_client
from src.access.permission import permission_service
from src.llm.web_search import web_search_service
from src.chat.service import chat_service
from src.llm.token_usage import token_usage_service

logger = logging.getLogger(__name__)


class RAGNonStreamingMixin:
    @staticmethod
    async def generate_response_non_streaming(
        user_message: str,
        session_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID],
        rag_enabled: bool = True,
        web_search_enabled: bool = False,
        document_source: str = "organization",
        selected_document_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Generate non-streaming response (for REST API fallback).
        """
        full_response = ""
        rag_results = []
        web_results = []
        sources = []
        prompt_tokens = 0
        completion_tokens = 0

        async for chunk in RAGService.generate_response(
            user_message=user_message,
            session_id=session_id,
            user_id=user_id,
            org_id=org_id,
            rag_enabled=rag_enabled,
            web_search_enabled=web_search_enabled,
            document_source=document_source,
            selected_document_ids=selected_document_ids
        ):
            if chunk["type"] == "rag_context":
                rag_results = chunk["data"]
            elif chunk["type"] == "web_search":
                web_results = chunk["data"]
            elif chunk["type"] == "reasoning":
                pass
            elif chunk["type"] == "chunk":
                full_response += chunk["content"]
            elif chunk["type"] == "done":
                sources = chunk.get("sources", [])
                prompt_tokens = chunk.get("prompt_tokens", 0)
                completion_tokens = chunk.get("completion_tokens", 0)
            elif chunk["type"] == "error":
                raise Exception(chunk["error"])

        return {
            "content": full_response,
            "rag_results": rag_results,
            "web_results": web_results,
            "sources": sources,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens
        }
