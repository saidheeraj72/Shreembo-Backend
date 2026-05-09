"""
Agentic RAG generation pipeline:
  Phase 1 — LLM decides what to search (Responses API tool calling, non-streaming)
  Phase 2 — Rerank retrieved chunks (FlashRank or BM25+RRF)
  Phase 3 — Stream final response with reasoning (Responses API, streaming)
"""
from typing import Optional, List, Dict, Any, AsyncGenerator
from uuid import UUID
import json
import logging

from src.core.openai_client import openai_client
from src.core.database import db
from src.access.permission import permission_service
from src.llm.web_search import web_search_service
from src.chat.service import chat_service
from src.llm.token_usage import token_usage_service
from src.llm import reranker
from src.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions — Responses API format (name/description at top level)
# ---------------------------------------------------------------------------

_SEARCH_DOCUMENTS_TOOL = {
    "type": "function",
    "name": "search_documents",
    "description": (
        "Search the user's uploaded documents for relevant information. "
        "Call multiple times with different query angles to cover complex questions. "
        "Do NOT call for greetings, simple follow-ups, or questions answerable from chat history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Precise, keyword-rich search query. "
                    "Rephrase the user's question as specific terms that would appear in the document."
                ),
            },
            "top_k": {
                "type": "integer",
                "description": (
                    "Number of document chunks to retrieve. "
                    "Use 5–8 for specific facts, 10–15 for broad topics. Default 8."
                ),
                "default": 8,
            },
        },
        "required": ["query"],
    },
}

_SEARCH_WEB_TOOL = {
    "type": "function",
    "name": "search_web",
    "description": "Search the web for current events or general knowledge not available in uploaded documents.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Web search query"},
            "limit": {"type": "integer", "description": "Max results. Default 5.", "default": 5},
        },
        "required": ["query"],
    },
}

_LIST_DOCUMENTS_TOOL = {
    "type": "function",
    "name": "list_documents",
    "description": (
        "List available documents the user has uploaded. "
        "Use this to discover which documents exist before running a targeted search, "
        "or when the user asks what files/documents are available."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max documents to return (1–50). Default 20.",
                "default": 20,
            },
            "offset": {
                "type": "integer",
                "description": "Number of documents to skip for pagination. Default 0.",
                "default": 0,
            },
        },
        "required": [],
    },
}

# Lightweight routing prompt — only used in Phase 1
_TOOL_ROUTING_INSTRUCTIONS = """\
Decide which tools to call in order to answer the user's question accurately.

Guidelines:
- Call list_documents when the user asks what files or documents are available, \
or when you need to discover document names before doing a targeted search.
- Call search_documents when the answer likely lives in the user's uploaded documents \
(reports, policies, contracts, data, etc.). You may call it several times with \
different precise queries to cover different aspects of a complex question.
- Call search_web only for real-time or general knowledge clearly absent from documents.
- Do NOT call any tool for greetings, thanks, simple follow-ups, or questions you can \
answer from the conversation history alone.
- Choose top_k deliberately: 5–8 for narrow lookups, 10–15 for broad topics."""


# ---------------------------------------------------------------------------
# Helper — list accessible documents with pagination
# ---------------------------------------------------------------------------

async def _list_accessible_documents(
    user_id: UUID,
    org_id: Optional[UUID],
    session_id: Optional[UUID],
    document_source: str,
    limit: int,
    offset: int,
) -> Dict[str, Any]:
    """
    Return a paginated list of documents the user can access.
    Includes org/personal documents (storage_nodes) and session documents.
    """
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    docs: List[dict] = []

    # ── Org / personal documents ──────────────────────────────────────────
    try:
        namespace_is_org = org_id and document_source != "personal"

        if namespace_is_org:
            is_admin = await permission_service.is_admin_or_owner(user_id, org_id)
            query = (
                db.admin.table("storage_nodes")
                .select("id, name, file_extension, description, tags, embedding_status")
                .eq("org_id", str(org_id))
                .eq("status", "active")
                .eq("node_type", "file")
            )
            if not is_admin:
                accessible_folder_ids = await permission_service.get_accessible_folder_ids(
                    user_id, org_id
                )
                if accessible_folder_ids:
                    query = query.in_("parent_id", list(accessible_folder_ids))
                else:
                    query = None  # no accessible folders
        else:
            query = (
                db.admin.table("storage_nodes")
                .select("id, name, file_extension, description, tags, embedding_status")
                .eq("owner_id", str(user_id))
                .is_("org_id", "null")
                .eq("status", "active")
                .eq("node_type", "file")
            )

        if query is not None:
            result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
            for d in (result.data or []):
                docs.append({
                    "id": d["id"],
                    "name": d["name"],
                    "type": d.get("file_extension") or "unknown",
                    "description": d.get("description") or "",
                    "tags": d.get("tags") or [],
                    "embedding_status": d.get("embedding_status") or "unknown",
                    "source": "organization" if namespace_is_org else "personal",
                })
    except Exception as e:
        logger.error("list_documents: storage_nodes query failed: %s", e)

    # ── Session documents ─────────────────────────────────────────────────
    if session_id:
        try:
            sess_result = (
                db.admin.table("session_documents")
                .select("id, filename, file_type, embedding_status")
                .eq("session_id", str(session_id))
                .eq("embedding_status", "completed")
                .order("created_at", desc=True)
                .range(0, limit - 1)
                .execute()
            )
            for d in (sess_result.data or []):
                docs.append({
                    "id": d["id"],
                    "name": d["filename"],
                    "type": d.get("file_type") or "unknown",
                    "description": "",
                    "tags": [],
                    "embedding_status": d.get("embedding_status") or "completed",
                    "source": "session",
                })
        except Exception as e:
            logger.error("list_documents: session_documents query failed: %s", e)

    return {"documents": docs, "count": len(docs), "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------

class RAGGenerationMixin:

    @staticmethod
    def build_context(
        rag_results: List[dict],
        web_results: Optional[List[dict]] = None,
    ) -> str:
        """Build context string from reranked RAG chunks and web results."""
        parts: List[str] = []

        if rag_results:
            parts.append("## Relevant Document Excerpts:\n")
            for i, r in enumerate(rag_results, 1):
                parts.append(
                    f"\n### Source {i}: {r['document_name']}\n"
                    f"{r['chunk_text']}\n"
                )

        if web_results:
            parts.append("\n## Web Search Results:\n")
            for r in web_results:
                parts.append(
                    f"\n### {r['title']}\n"
                    f"URL: {r['url']}\n"
                    f"{r['snippet']}\n"
                )

        return "".join(parts)

    @staticmethod
    async def generate_response(
        user_message: str,
        session_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID],
        rag_enabled: bool = True,
        web_search_enabled: bool = False,
        document_source: str = "organization",
        selected_document_ids: Optional[List[str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Agentic RAG pipeline. Yields dicts with keys:
          type: tool_start | tool_done | rag_context | web_search |
                reasoning | chunk | done | error
        """
        client = openai_client.client
        all_rag_results: List[dict] = []
        all_web_results: List[dict] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0

        history = await chat_service.get_chat_history(
            session_id, limit=settings.CHAT_HISTORY_LIMIT
        )
        input_messages = list(history) + [{"role": "user", "content": user_message}]

        try:
            # ── Phase 1: Tool-Calling Decision ────────────────────────────────
            available_tools: List[dict] = []
            # Session docs are always searchable; org/personal docs need rag_enabled
            if rag_enabled or session_id:
                available_tools.append(_LIST_DOCUMENTS_TOOL)
                available_tools.append(_SEARCH_DOCUMENTS_TOOL)
            if web_search_enabled and settings.SERPER_API_KEY:
                available_tools.append(_SEARCH_WEB_TOOL)

            if available_tools:
                tool_response = await client.responses.create(
                    model=settings.OPENAI_CHAT_MODEL,
                    instructions=_TOOL_ROUTING_INSTRUCTIONS,
                    input=input_messages,
                    tools=available_tools,
                    tool_choice="auto",
                )
                if hasattr(tool_response, "usage") and tool_response.usage:
                    total_prompt_tokens += tool_response.usage.input_tokens
                    total_completion_tokens += tool_response.usage.output_tokens

                function_calls = [
                    item for item in tool_response.output
                    if getattr(item, "type", None) == "function_call"
                ]

                seen_keys: set = set()

                for call in function_calls:
                    args = json.loads(call.arguments)

                    # ── search_documents ──────────────────────────────────────
                    if call.name == "search_documents":
                        query = args.get("query") or user_message
                        top_k = max(3, min(int(args.get("top_k", 8)), 20))
                        fetch_k = top_k * settings.RAG_RETRIEVAL_TOP_K_MULTIPLIER

                        yield {"type": "tool_start", "name": "search_documents",
                               "query": query, "top_k": top_k}

                        try:
                            results = await RAGService.search_documents(
                                query=query,
                                user_id=user_id,
                                org_id=org_id,
                                session_id=session_id,
                                top_k=fetch_k,
                                document_source=document_source,
                                search_main=rag_enabled,
                                search_session=True,
                                selected_document_ids=selected_document_ids,
                            )
                        except Exception as e:
                            logger.error("search_documents failed: %s", e)
                            results = []

                        for r in results:
                            key = (r["document_id"], r["chunk_index"])
                            if key not in seen_keys:
                                seen_keys.add(key)
                                all_rag_results.append(r)

                        yield {"type": "tool_done", "name": "search_documents",
                               "count": len(results)}

                    # ── list_documents ────────────────────────────────────────
                    elif call.name == "list_documents":
                        limit = max(1, min(int(args.get("limit", 20)), 50))
                        offset = max(0, int(args.get("offset", 0)))

                        yield {"type": "tool_start", "name": "list_documents",
                               "limit": limit, "offset": offset}

                        try:
                            listing = await _list_accessible_documents(
                                user_id=user_id,
                                org_id=org_id,
                                session_id=session_id,
                                document_source=document_source,
                                limit=limit,
                                offset=offset,
                            )
                        except Exception as e:
                            logger.error("list_documents failed: %s", e)
                            listing = {"documents": [], "count": 0, "limit": limit, "offset": offset}

                        yield {"type": "tool_done", "name": "list_documents",
                               "count": listing["count"], "data": listing}

                    # ── search_web ────────────────────────────────────────────
                    elif call.name == "search_web":
                        query = args.get("query") or user_message
                        yield {"type": "tool_start", "name": "search_web", "query": query}

                        try:
                            web_results = await web_search_service.search(query)
                            all_web_results.extend(web_results)
                        except Exception as e:
                            logger.error("search_web failed: %s", e)
                            web_results = []

                        yield {"type": "tool_done", "name": "search_web",
                               "count": len(web_results)}

            # ── Phase 2: Reranking ────────────────────────────────────────────
            if all_rag_results:
                all_rag_results = reranker.rerank(
                    query=user_message,
                    chunks=all_rag_results,
                    top_k=settings.RAG_TOP_K,
                    min_score=settings.RAG_MIN_SCORE,
                )

            # Always emit rag_context (even empty) so frontend clears tool indicator
            yield {"type": "rag_context", "data": all_rag_results}

            if all_web_results:
                yield {"type": "web_search", "data": all_web_results}

            # ── Phase 3: Streaming Generation ────────────────────────────────
            context = RAGGenerationMixin.build_context(all_rag_results, all_web_results)

            instructions_parts = [settings.RAG_SYSTEM_PROMPT]
            if context:
                instructions_parts.append(
                    f"Here is relevant context to help answer the user's question:\n\n{context}"
                )

            gen_stream = await client.responses.create(
                model=settings.OPENAI_CHAT_MODEL,
                instructions="\n\n".join(instructions_parts),
                input=input_messages,
                reasoning={"effort": "low", "summary": "detailed"},
                max_output_tokens=settings.OPENAI_CHAT_MAX_TOKENS,
                stream=True,
            )

            full_response: List[str] = []
            async for event in gen_stream:
                if event.type == "response.reasoning_summary_text.delta":
                    yield {"type": "reasoning", "content": event.delta}
                elif event.type == "response.output_text.delta":
                    content = event.delta
                    full_response.append(content)
                    yield {"type": "chunk", "content": content}
                elif event.type == "response.completed":
                    usage = event.response.usage
                    total_prompt_tokens += usage.input_tokens
                    total_completion_tokens += usage.output_tokens

            sources = [
                {
                    "document_id": r["document_id"],
                    "document_name": r["document_name"],
                    "chunk_index": r["chunk_index"],
                    "chunk_text": r["chunk_text"],
                    "score": r["score"],
                    "source_type": r.get("source", "organization"),
                }
                for r in all_rag_results
            ]

            await token_usage_service.track_usage(
                user_id=user_id,
                org_id=org_id,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                is_rag=rag_enabled and bool(all_rag_results),
                is_web_search=web_search_enabled and bool(all_web_results),
            )

            yield {
                "type": "done",
                "content": "".join(full_response),
                "rag_results": all_rag_results,
                "web_results": all_web_results,
                "sources": sources,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
            }

        except Exception as e:
            logger.error("RAG generation error: %s", e, exc_info=True)
            yield {"type": "error", "error": str(e)}
