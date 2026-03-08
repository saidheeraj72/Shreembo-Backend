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


class RAGGenerationMixin:
    @staticmethod
    def build_context(
        rag_results: List[dict],
        web_results: Optional[List[dict]] = None,
    ) -> str:
        """Build context string from RAG and web search results."""
        context_parts = []

        # Add document context
        if rag_results:
            context_parts.append("## Relevant Document Excerpts:\n")
            for i, result in enumerate(rag_results, 1):
                context_parts.append(
                    f"\n### Source {i}: {result['document_name']}\n"
                    f"{result['chunk_text']}\n"
                )

        # Add web search context
        if web_results:
            context_parts.append("\n## Web Search Results:\n")
            for result in web_results:
                context_parts.append(
                    f"\n### {result['title']}\n"
                    f"URL: {result['url']}\n"
                    f"{result['snippet']}\n"
                )

        return "".join(context_parts)


    @staticmethod
    async def generate_response(
        user_message: str,
        session_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID],
        rag_enabled: bool = True,
        web_search_enabled: bool = False,
        document_source: str = "organization",
        selected_document_ids: Optional[List[str]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generate streaming response with RAG and optional web search.

        Yields dictionaries with:
        - type: 'rag_context' | 'web_search' | 'chunk' | 'done' | 'error'
        - content/data based on type
        """
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        rag_results = []
        web_results = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        
        # Get chat history first for context (needed for both routing and generation)
        history = await chat_service.get_chat_history(
            session_id,
            limit=settings.CHAT_HISTORY_LIMIT
        )

        try:
            # 1. RAG Search - Get top 5 chunks
            # Logic: 
            #   - search_main: controlled by rag_enabled (search org/personal documents)
            #   - search_session: always True when session_id exists (session documents should always be searched)
            search_main = rag_enabled
            search_session = True  # Always search session documents when in a chat session

            rag_results = await RAGService.search_documents(
                query=user_message,
                user_id=user_id,
                org_id=org_id,
                session_id=session_id,
                top_k=20,  # Get top 20 chunks
                document_source=document_source,
                search_main=search_main,
                search_session=search_session,
                selected_document_ids=selected_document_ids
            )
            
            if rag_results:
                yield {
                    "type": "rag_context",
                    "data": rag_results
                }

            # 2. Web Search (Conditional - could also add routing here)
            if web_search_enabled and settings.SERPER_API_KEY:
                # For now, if enabled, we search. Could add similar "should_web_search" logic.
                web_results = await web_search_service.search(user_message)
                if web_results:
                    yield {
                        "type": "web_search",
                        "data": web_results
                    }

            # 3. Build context
            context = RAGService.build_context(rag_results, web_results)

            # 4. Build messages
            messages = [
                {"role": "system", "content": settings.RAG_SYSTEM_PROMPT}
            ]

            if context:
                messages.append({
                    "role": "system",
                    "content": f"Here is relevant context to help answer the user's question:\n\n{context}"
                })

            messages.extend(history)
            messages.append({"role": "user", "content": user_message})

            # 5. Stream response
            stream = await client.chat.completions.create(
                model=settings.OPENAI_CHAT_MODEL,
                messages=messages,
                max_tokens=settings.OPENAI_CHAT_MAX_TOKENS,
                temperature=settings.OPENAI_CHAT_TEMPERATURE,
                stream=True,
                stream_options={"include_usage": True}
            )

            full_response = []
            async for chunk in stream:
                # Check for usage in the final chunk
                if hasattr(chunk, 'usage') and chunk.usage:
                    total_prompt_tokens = chunk.usage.prompt_tokens
                    total_completion_tokens = chunk.usage.completion_tokens

                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response.append(content)
                    yield {
                        "type": "chunk",
                        "content": content
                    }

            # Build sources for response with full chunk text and metadata
            sources = [
                {
                    "document_id": r["document_id"],
                    "document_name": r["document_name"],
                    "chunk_index": r["chunk_index"],
                    "chunk_text": r["chunk_text"],
                    "score": r["score"],
                    "source_type": r.get("source", "organization"),
                }
                for r in rag_results
            ]

            # Track token usage
            await token_usage_service.track_usage(
                user_id=user_id,
                org_id=org_id,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                is_rag=rag_enabled and bool(rag_results),
                is_web_search=web_search_enabled and bool(web_results)
            )

            # 6. Final result
            yield {
                "type": "done",
                "content": "".join(full_response),
                "rag_results": rag_results,
                "web_results": web_results,
                "sources": sources,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens
            }

        except Exception as e:
            yield {
                "type": "error",
                "error": str(e)
            }

