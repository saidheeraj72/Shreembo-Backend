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

        # Separate results from main index and chat-sessions index
        main_results = []
        session_results = []

        # Query main index (organization or personal documents)
        namespace = str(org_id) if org_id else str(user_id)
        print(f"RAG: Searching main index in namespace: {namespace}, top_k: {top_k}")

        results = await pinecone_client.query(
            vector=query_embedding,
            namespace=namespace,
            top_k=top_k * 2,  # Get extra for filtering
            filter=None
        )
        if results:
            main_results = results
            print(f"RAG: Got {len(results)} results from main index")

        # Also query chat-sessions index if we have a session
        if session_id:
            print(f"RAG: Searching chat-sessions index for session: {session_id}")
            
            # Determine namespace for session documents
            # Default to current user (uploader)
            session_namespace = str(user_id)
            
            try:
                # Fetch session to get the owner (documents are stored in owner's namespace)
                # Use db.admin to bypass RLS since we've already validated access in the API layer
                session_result = db.admin.table("chat_sessions").select("user_id").eq(
                    "id", str(session_id)
                ).single().execute()
                
                if session_result and session_result.data:
                    # Use Session Owner's ID as namespace
                    session_namespace = str(session_result.data["user_id"])
            except Exception as e:
                print(f"RAG: Error fetching session owner: {e}")

            session_index_results = await pinecone_client.query(
                vector=query_embedding,
                namespace=session_namespace,
                top_k=top_k * 2,
                filter={'session_id': str(session_id)},  # Filter by session for isolation
                index_name=settings.PINECONE_CHAT_SESSIONS_INDEX
            )
            if session_index_results:
                session_results = session_index_results
                print(f"RAG: Got {len(session_index_results)} results from chat-sessions index (namespace: {session_namespace})")

        if not main_results and not session_results:
            print(f"RAG: No results from Pinecone")
            return []

        # Process main index results with permission checks
        main_filtered_results = []
        if main_results:
            # Extract document IDs from main results
            doc_ids = [r.metadata.get('document_id') for r in main_results if r.metadata and r.metadata.get('document_id')]
            unique_doc_ids = list(set(doc_ids))

            # Filter by permissions
            accessible_doc_ids = await RAGService.get_accessible_documents_for_rag(
                user_id, org_id, unique_doc_ids
            )
            print(f"RAG: {len(accessible_doc_ids)} main index documents accessible after permission check")

            if accessible_doc_ids:
                # Get document details from storage_nodes
                docs_result = db.admin.table("storage_nodes").select(
                    "id, name"
                ).in_("id", accessible_doc_ids).eq("status", "active").execute()

                doc_map = {d['id']: d['name'] for d in docs_result.data if docs_result and docs_result.data}
                print(f"RAG: Found {len(doc_map)} active main index documents in database")

                # Build results from main index
                for r in main_results:
                    doc_id = r.metadata.get('document_id')
                    if doc_id in accessible_doc_ids and doc_id in doc_map:
                        main_filtered_results.append({
                            'document_id': doc_id,
                            'document_name': doc_map.get(doc_id, 'Unknown'),
                            'chunk_text': r.metadata.get('chunk_text', ''),
                            'chunk_index': r.metadata.get('chunk_index', 0),
                            'score': r.score
                        })

        # Process session index results - no permission check needed (session isolation is enough)
        session_filtered_results = []
        if session_results:
            # Get unique session document IDs from session results
            session_doc_ids = list(set([r.metadata.get('document_id') for r in session_results if r.metadata and r.metadata.get('document_id')]))

            # Get document names from session_documents table (only completed embeddings)
            session_docs_result = db.admin.table("session_documents").select(
                "id, filename"
            ).in_("id", session_doc_ids).eq(
                "session_id", str(session_id)
            ).eq(
                "embedding_status", "completed"  # Only use documents with completed embeddings
            ).execute()

            session_doc_map = {d['id']: d['filename'] for d in session_docs_result.data if session_docs_result and session_docs_result.data}
            print(f"RAG: Found {len(session_doc_map)} completed session documents (out of {len(session_doc_ids)} total)")

            # Build results from session index
            for r in session_results:
                session_doc_id = r.metadata.get('document_id')
                if session_doc_id in session_doc_map:
                    session_filtered_results.append({
                        'document_id': session_doc_id,  # This is actually session_document_id
                        'document_name': session_doc_map.get(session_doc_id, 'Unknown'),
                        'chunk_text': r.metadata.get('chunk_text', ''),
                        'chunk_index': r.metadata.get('chunk_index', 0),
                        'score': r.score,
                        'source': 'session'  # Mark as session document
                    })

        # Combine and sort by score
        all_filtered_results = main_filtered_results + session_filtered_results
        all_filtered_results.sort(key=lambda x: x['score'], reverse=True)

        # Take top K
        final_results = all_filtered_results[:top_k]

        print(f"RAG: Returning {len(final_results)} chunks ({len([r for r in final_results if r.get('source') == 'session'])} from session, {len([r for r in final_results if not r.get('source')])} from main index)")
        if final_results:
            print(f"RAG: Score range: {final_results[0]['score']:.3f} to {final_results[-1]['score']:.3f}")

        return final_results

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
