"""
Agentic RAG generation pipeline:
  Phase 1 — LLM decides what to search (Responses API tool calling, non-streaming)
  Phase 2 — Rerank retrieved chunks (FlashRank or BM25+RRF)
  Phase 3 — Stream final response with reasoning (Responses API, streaming)
"""
from typing import Optional, List, Dict, Any, AsyncGenerator
from uuid import UUID
import ast
import json
import logging
import math
import operator

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

_FIND_DOCUMENT_BY_NAME_TOOL = {
    "type": "function",
    "name": "find_document_by_name",
    "description": (
        "Search for documents by filename, description, or tags — metadata only, not content. "
        "Use when the user references a document by name (e.g. 'the contract', 'my Q3 report') "
        "to confirm it exists and get its exact name before reading it. "
        "Unlike search_documents (which searches content), this searches file metadata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Term to match against document names, descriptions, and tags.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (1–20). Default 10.",
                "default": 10,
            },
        },
        "required": ["query"],
    },
}

_GET_DOCUMENT_CONTENT_TOOL = {
    "type": "function",
    "name": "get_document_content",
    "description": (
        "Retrieve the full text of a specific document by name. "
        "Use when the user asks to read, summarize, or deeply analyze a specific file. "
        "Prefer search_documents when you only need relevant excerpts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_name": {
                "type": "string",
                "description": "Name or partial name of the document to retrieve.",
            },
            "max_chunks": {
                "type": "integer",
                "description": "Maximum text chunks to return (1–100). Default 40.",
                "default": 40,
            },
        },
        "required": ["document_name"],
    },
}

_CALCULATE_TOOL = {
    "type": "function",
    "name": "calculate",
    "description": (
        "Evaluate a mathematical expression and return the exact result. "
        "Use for any arithmetic, percentage, or numeric computation — do NOT estimate in your head. "
        "Supports: +, -, *, /, ** (power), % (modulo), abs(), round(), min(), max(), "
        "sqrt(), log(), ceil(), floor(), and constants pi and e."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": (
                    "Math expression to evaluate, e.g. '42500 * 0.15' or 'round(1234567 / 12, 2)'."
                ),
            },
        },
        "required": ["expression"],
    },
}

# Lightweight routing prompt — only used in Phase 1
_TOOL_ROUTING_INSTRUCTIONS = """\
Decide which tools to call in order to answer the user's question accurately.

Guidelines:
- Call find_document_by_name when the user references a document by name or topic \
to confirm it exists and get its exact filename before reading it.
- Call get_document_content when the user wants to read, analyze, or summarize a \
specific document and you know (or just found) its name. Retrieves the full text.
- Call list_documents when the user asks for a broad overview of all available files.
- Call search_documents when the answer likely lives in the user's uploaded documents \
(reports, policies, contracts, data, etc.). Call it several times with different \
precise queries to cover complex questions.
- Call search_web only for real-time or general knowledge clearly absent from documents.
- Call calculate for any arithmetic, percentage, or numeric computation needed to answer \
the user — never approximate numbers in your head.
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
                .order("uploaded_at", desc=True)
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


async def _find_document_by_name(
    user_id: UUID,
    org_id: Optional[UUID],
    session_id: Optional[UUID],
    document_source: str,
    query: str,
    limit: int = 10,
) -> Dict[str, Any]:
    """Search storage_nodes by filename, description, or tags (metadata only)."""
    limit = max(1, min(limit, 20))
    namespace_is_org = org_id and document_source != "personal"
    docs: List[dict] = []
    seen_ids: set = set()

    def _base_query(ilike_col: str):
        q = (
            db.admin.table("storage_nodes")
            .select("id, name, file_extension, description, tags, embedding_status")
            .eq("status", "active")
            .eq("node_type", "file")
            .ilike(ilike_col, f"%{query}%")
        )
        if namespace_is_org:
            return q.eq("org_id", str(org_id))
        return q.eq("owner_id", str(user_id)).is_("org_id", "null")

    try:
        for col in ("name", "description"):
            if len(docs) >= limit:
                break
            result = _base_query(col).limit(limit).execute()
            for d in (result.data or []):
                if d["id"] not in seen_ids:
                    seen_ids.add(d["id"])
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
        logger.error("find_document_by_name: DB query failed: %s", e)
        return {"error": str(e), "documents": [], "count": 0, "query": query}

    if session_id and len(docs) < limit:
        try:
            sess_result = (
                db.admin.table("session_documents")
                .select("id, filename, file_type, embedding_status")
                .eq("session_id", str(session_id))
                .ilike("filename", f"%{query}%")
                .eq("embedding_status", "completed")
                .limit(limit - len(docs))
                .execute()
            )
            for d in (sess_result.data or []):
                if d["id"] not in seen_ids:
                    seen_ids.add(d["id"])
                    docs.append({
                        "id": d["id"],
                        "name": d["filename"],
                        "type": d.get("file_type") or "unknown",
                        "description": "",
                        "tags": [],
                        "embedding_status": "completed",
                        "source": "session",
                    })
        except Exception as e:
            logger.error("find_document_by_name: session docs query failed: %s", e)

    return {"documents": docs[:limit], "count": len(docs[:limit]), "query": query}


async def _get_document_content(
    user_id: UUID,
    org_id: Optional[UUID],
    session_id: Optional[UUID],
    document_source: str,
    document_name: str,
    max_chunks: int = 40,
) -> Dict[str, Any]:
    """Retrieve all text chunks for a named document from Qdrant."""
    from src.core.qdrant_client import qdrant_client

    max_chunks = max(1, min(max_chunks, 100))
    namespace_is_org = org_id and document_source != "personal"

    # Locate document in storage_nodes by partial name match
    try:
        q = (
            db.admin.table("storage_nodes")
            .select("id, name, file_extension")
            .ilike("name", f"%{document_name}%")
            .eq("status", "active")
            .eq("node_type", "file")
        )
        if namespace_is_org:
            q = q.eq("org_id", str(org_id))
        else:
            q = q.eq("owner_id", str(user_id)).is_("org_id", "null")

        result = q.limit(5).execute()
        matches = result.data or []
    except Exception as e:
        logger.error("get_document_content: DB lookup failed: %s", e)
        return {"error": f"Document lookup failed: {e}", "chunks": [], "document_name": document_name}

    # Also check session documents
    if not matches and session_id:
        try:
            sess_result = (
                db.admin.table("session_documents")
                .select("id, filename")
                .eq("session_id", str(session_id))
                .ilike("filename", f"%{document_name}%")
                .eq("embedding_status", "completed")
                .limit(1)
                .execute()
            )
            if sess_result.data:
                d = sess_result.data[0]
                chunks = await qdrant_client.scroll_by_document(
                    document_id=d["id"],
                    namespace=str(user_id),
                    limit=max_chunks,
                    index_name=settings.QDRANT_SESSIONS_COLLECTION,
                )
                return {
                    "document_id": d["id"],
                    "document_name": d["filename"],
                    "chunks": chunks,
                    "total_chunks": len(chunks),
                    "source": "session",
                }
        except Exception as e:
            logger.error("get_document_content: session doc lookup failed: %s", e)

    if not matches:
        return {
            "error": f"No document found matching '{document_name}'",
            "chunks": [],
            "document_name": document_name,
        }

    doc = matches[0]
    doc_id = doc["id"]
    real_name = doc["name"]

    # Permission check for org documents
    if namespace_is_org:
        accessible = await RAGService.get_accessible_documents_for_rag(user_id, org_id, [doc_id])
        if not accessible:
            return {
                "error": f"Access denied to '{real_name}'",
                "chunks": [],
                "document_name": real_name,
            }

    namespace = str(org_id) if namespace_is_org else str(user_id)
    chunks = await qdrant_client.scroll_by_document(
        document_id=doc_id,
        namespace=namespace,
        limit=max_chunks,
    )

    return {
        "document_id": doc_id,
        "document_name": real_name,
        "chunks": chunks,
        "total_chunks": len(chunks),
    }


# ---------------------------------------------------------------------------
# Safe math calculator
# ---------------------------------------------------------------------------

_CALC_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_CALC_FUNCS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "int": int, "float": float,
    "sqrt": math.sqrt, "pow": math.pow,
    "log": math.log, "log10": math.log10,
    "ceil": math.ceil, "floor": math.floor,
}

_CALC_NAMES = {"pi": math.pi, "e": math.e}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported literal: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op_fn = _CALC_OPS.get(type(node.op))
        if not op_fn:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op_fn(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op_fn = _CALC_OPS.get(type(node.op))
        if not op_fn:
            raise ValueError(f"Unsupported unary: {type(node.op).__name__}")
        return op_fn(_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls allowed")
        fn = _CALC_FUNCS.get(node.func.id)
        if not fn:
            raise ValueError(f"Function '{node.func.id}' not allowed")
        return fn(*[_eval_node(a) for a in node.args])
    if isinstance(node, ast.Name):
        if node.id in _CALC_NAMES:
            return _CALC_NAMES[node.id]
        raise ValueError(f"Name '{node.id}' not allowed")
    raise ValueError(f"Unsupported node: {type(node).__name__}")


def _safe_calculate(expression: str) -> Dict[str, Any]:
    """Safely evaluate a math expression using AST whitelisting."""
    if len(expression) > 500:
        return {"error": "Expression too long", "expression": expression}
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval_node(tree.body)
        if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
            formatted = str(int(result))
        elif isinstance(result, float):
            formatted = f"{result:.10g}"
        else:
            formatted = str(result)
        return {"expression": expression, "result": result, "formatted": formatted}
    except ZeroDivisionError:
        return {"error": "Division by zero", "expression": expression}
    except Exception as e:
        return {"error": str(e), "expression": expression}


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------

class RAGGenerationMixin:

    @staticmethod
    def build_context(
        rag_results: List[dict],
        web_results: Optional[List[dict]] = None,
        document_contents: Optional[List[dict]] = None,
        calculation_results: Optional[List[dict]] = None,
    ) -> str:
        """Build context string from all tool results."""
        parts: List[str] = []

        if rag_results:
            parts.append("## Relevant Document Excerpts:\n")
            for i, r in enumerate(rag_results, 1):
                parts.append(
                    f"\n### Source {i}: {r['document_name']}\n"
                    f"{r['chunk_text']}\n"
                )

        if document_contents:
            parts.append("\n## Full Document Contents:\n")
            for dc in document_contents:
                if dc.get("error"):
                    parts.append(f"\n### {dc.get('document_name', 'Unknown')}\nNote: {dc['error']}\n")
                else:
                    parts.append(f"\n### {dc['document_name']}\n")
                    for chunk in dc.get("chunks", []):
                        parts.append(chunk["chunk_text"])
                        parts.append("\n")

        if web_results:
            parts.append("\n## Web Search Results:\n")
            for r in web_results:
                parts.append(
                    f"\n### {r['title']}\n"
                    f"URL: {r['url']}\n"
                    f"{r['snippet']}\n"
                )

        if calculation_results:
            parts.append("\n## Calculation Results:\n")
            for c in calculation_results:
                if c.get("error"):
                    parts.append(f"- {c['expression']} → Error: {c['error']}\n")
                else:
                    parts.append(f"- {c['expression']} = {c['formatted']}\n")

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
        all_document_contents: List[dict] = []
        all_calculation_results: List[dict] = []
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
                available_tools.append(_FIND_DOCUMENT_BY_NAME_TOOL)
                available_tools.append(_GET_DOCUMENT_CONTENT_TOOL)
                available_tools.append(_SEARCH_DOCUMENTS_TOOL)
            if web_search_enabled and settings.SERPER_API_KEY:
                available_tools.append(_SEARCH_WEB_TOOL)
            available_tools.append(_CALCULATE_TOOL)  # always available

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

                    # ── find_document_by_name ─────────────────────────────────
                    elif call.name == "find_document_by_name":
                        name_query = args.get("query", "")
                        limit = max(1, min(int(args.get("limit", 10)), 20))

                        yield {"type": "tool_start", "name": "find_document_by_name",
                               "query": name_query}

                        try:
                            name_results = await _find_document_by_name(
                                user_id=user_id,
                                org_id=org_id,
                                session_id=session_id,
                                document_source=document_source,
                                query=name_query,
                                limit=limit,
                            )
                        except Exception as e:
                            logger.error("find_document_by_name failed: %s", e)
                            name_results = {"documents": [], "count": 0, "query": name_query}

                        yield {"type": "tool_done", "name": "find_document_by_name",
                               "count": name_results["count"], "data": name_results}

                    # ── get_document_content ──────────────────────────────────
                    elif call.name == "get_document_content":
                        doc_name = args.get("document_name", "")
                        max_chunks = max(1, min(int(args.get("max_chunks", 40)), 100))

                        yield {"type": "tool_start", "name": "get_document_content",
                               "query": doc_name}

                        try:
                            doc_content = await _get_document_content(
                                user_id=user_id,
                                org_id=org_id,
                                session_id=session_id,
                                document_source=document_source,
                                document_name=doc_name,
                                max_chunks=max_chunks,
                            )
                        except Exception as e:
                            logger.error("get_document_content failed: %s", e)
                            doc_content = {"error": str(e), "chunks": [], "document_name": doc_name}

                        all_document_contents.append(doc_content)
                        yield {"type": "tool_done", "name": "get_document_content",
                               "count": len(doc_content.get("chunks", [])),
                               "document_name": doc_content.get("document_name", doc_name)}

                    # ── calculate ─────────────────────────────────────────────
                    elif call.name == "calculate":
                        expression = args.get("expression", "")
                        yield {"type": "tool_start", "name": "calculate",
                               "query": expression}

                        calc_result = _safe_calculate(expression)
                        all_calculation_results.append(calc_result)

                        yield {"type": "tool_done", "name": "calculate",
                               "result": calc_result.get("formatted") or calc_result.get("error", "")}

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
            context = RAGGenerationMixin.build_context(
                all_rag_results,
                all_web_results,
                all_document_contents or None,
                all_calculation_results or None,
            )

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
