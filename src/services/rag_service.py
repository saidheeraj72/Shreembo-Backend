"""RAG (Retrieval-Augmented Generation) service."""
from typing import Optional, List, Dict, Any, AsyncGenerator
from uuid import UUID
import json

from openai import AsyncOpenAI

from src.core.database import db
from src.core.openai_client import openai_client
from src.core.pinecone_client import pinecone_client
from src.services.permission_service import permission_service
from src.services.web_search_service import web_search_service
from src.services.chat_service import chat_service
from src.services.token_usage_service import token_usage_service
from src.config import settings


class RAGService:
    """Service for RAG pipeline operations."""

    @staticmethod
    async def get_accessible_documents_for_rag(
        user_id: UUID,
        org_id: Optional[UUID],
        document_ids: List[str]
    ) -> List[str]:
        """
        Filter document IDs to only those the user can access.

        For org users: Check folder-level permissions
        For personal users: All documents in their namespace are accessible
        """
        if not document_ids:
            return []

        if not org_id:
            # Personal user - all docs in their namespace are theirs
            return document_ids

        # Org user - check permissions
        is_admin = await permission_service.is_admin_or_owner(user_id, org_id)
        if is_admin:
            return document_ids  # Admin/Owner sees all

        # Get accessible folder IDs
        accessible_folder_ids = await permission_service.get_accessible_folder_ids(
            user_id, org_id
        )

        if not accessible_folder_ids:
            return []

        # Fetch documents and filter by parent_id
        docs_result = db.admin.table("storage_nodes").select(
            "id, parent_id"
        ).in_("id", document_ids).execute()

        accessible = []
        for doc in docs_result.data:
            parent_id = doc.get("parent_id")
            # Document is accessible if its parent folder is in accessible set
            # or if it's at root level (parent_id is None) - depends on your policy
            if parent_id and parent_id in accessible_folder_ids:
                accessible.append(doc["id"])
            elif not parent_id:
                # Root level - you may want to adjust this based on your policy
                # For now, allow root-level docs for org members
                accessible.append(doc["id"])

        return accessible

    @staticmethod
    async def search_documents(
        query: str,
        user_id: UUID,
        org_id: Optional[UUID],
        session_id: Optional[UUID] = None,
        top_k: int = None
    ) -> List[dict]:
        """
        Search documents using vector similarity with permission filtering.

        Args:
            query: Search query
            user_id: User performing search
            org_id: Organization context (None for personal)
            session_id: Optional session to include session documents
            top_k: Number of results to return
        """
        top_k = top_k or settings.RAG_TOP_K

        # Get query embedding
        query_embedding = await openai_client.get_embedding(query)

        if not query_embedding:
            print(f"RAG: Failed to get embedding for query: {query}")
            return []

        # Determine namespace
        namespace = str(org_id) if org_id else str(user_id)
        print(f"RAG: Searching in namespace: {namespace}, top_k: {top_k}")

        # Query Pinecone
        results = await pinecone_client.query(
            vector=query_embedding,
            namespace=namespace,
            top_k=top_k * 2,  # Get extra for filtering
            filter=None
        )

        if not results:
            print(f"RAG: No results from Pinecone")
            return []

        print(f"RAG: Got {len(results)} results from Pinecone")

        # Extract all document IDs from results
        doc_ids = [r.metadata.get('document_id') for r in results if r.metadata and r.metadata.get('document_id')]
        unique_doc_ids = list(set(doc_ids))

        # Filter by permissions
        accessible_doc_ids = await RAGService.get_accessible_documents_for_rag(
            user_id, org_id, unique_doc_ids
        )
        print(f"RAG: {len(accessible_doc_ids)} documents accessible after permission check")

        if not accessible_doc_ids:
            return []

        # Get document details
        docs_result = db.admin.table("storage_nodes").select(
            "id, name"
        ).in_("id", accessible_doc_ids).eq("status", "active").execute()

        doc_map = {d['id']: d['name'] for d in docs_result.data if docs_result and docs_result.data}
        print(f"RAG: Found {len(doc_map)} active documents in database")

        # Build results - just take top K chunks directly
        filtered_results = []

        for r in results:
            doc_id = r.metadata.get('document_id')

            # Only check if document is accessible
            if doc_id not in accessible_doc_ids:
                continue

            filtered_results.append({
                'document_id': doc_id,
                'document_name': doc_map.get(doc_id, 'Unknown'),
                'chunk_text': r.metadata.get('chunk_text', ''),
                'chunk_index': r.metadata.get('chunk_index', 0),
                'score': r.score
            })

            # Stop when we have top_k chunks
            if len(filtered_results) >= top_k:
                break

        print(f"RAG: Returning {len(filtered_results)} chunks")
        if filtered_results:
            print(f"RAG: Score range: {filtered_results[0]['score']:.3f} to {filtered_results[-1]['score']:.3f}")

        return filtered_results

    @staticmethod
    def build_context(
        rag_results: List[dict],
        web_results: Optional[List[dict]] = None,
        max_length: int = None
    ) -> str:
        """Build context string from RAG and web search results."""
        max_length = max_length or settings.RAG_MAX_CONTEXT_LENGTH
        context_parts = []
        current_length = 0

        # Add document context
        if rag_results:
            context_parts.append("## Relevant Document Excerpts:\n")
            for i, result in enumerate(rag_results, 1):
                chunk = (
                    f"\n### Source {i}: {result['document_name']}\n"
                    f"{result['chunk_text']}\n"
                )
                if current_length + len(chunk) > max_length:
                    break
                context_parts.append(chunk)
                current_length += len(chunk)

        # Add web search context
        if web_results:
            context_parts.append("\n## Web Search Results:\n")
            for result in web_results:
                chunk = (
                    f"\n### {result['title']}\n"
                    f"URL: {result['url']}\n"
                    f"{result['snippet']}\n"
                )
                if current_length + len(chunk) > max_length:
                    break
                context_parts.append(chunk)
                current_length += len(chunk)

        return "".join(context_parts)


    @staticmethod
    async def generate_response(
        user_message: str,
        session_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID],
        rag_enabled: bool = True,
        web_search_enabled: bool = False
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
            if rag_enabled:
                rag_results = await RAGService.search_documents(
                    query=user_message,
                    user_id=user_id,
                    org_id=org_id,
                    session_id=session_id,
                    top_k=5  # Get top 5 chunks
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

            # Build sources for response
            sources = [
                {
                    "document_id": r["document_id"],
                    "document_name": r["document_name"],
                    "chunk_index": r["chunk_index"],
                    "chunk_text": r["chunk_text"][:200],  # Truncate for response
                    "score": r["score"]
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

    @staticmethod
    async def generate_response_non_streaming(
        user_message: str,
        session_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID],
        rag_enabled: bool = True,
        web_search_enabled: bool = False
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
            web_search_enabled=web_search_enabled
        ):
            if chunk["type"] == "rag_context":
                rag_results = chunk["data"]
            elif chunk["type"] == "web_search":
                web_results = chunk["data"]
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


rag_service = RAGService()
