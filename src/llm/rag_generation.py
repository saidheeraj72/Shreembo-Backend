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
from src.llm.citations import strip_invalid_citations
from src.llm.judge import judge_answer
from src.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token accounting for the context budget
# ---------------------------------------------------------------------------

_ctx_enc = None


def _token_len(text: str) -> int:
    global _ctx_enc
    if _ctx_enc is None:
        import tiktoken
        _ctx_enc = tiktoken.get_encoding("cl100k_base")
    return len(_ctx_enc.encode(text, disallowed_special=()))


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
- Choose top_k deliberately: 5–8 for narrow lookups, 10–15 for broad topics.

You may be shown the results of your previous tool calls. If they already answer the \
question, call no further tools. If a search came back empty or off-target, try again \
with different wording — a filename, a synonym, or a more specific phrase — rather than \
repeating the same query."""


# ---------------------------------------------------------------------------
# Helper — list accessible documents with pagination
# ---------------------------------------------------------------------------

async def _list_accessible_documents(
    user_id: UUID,
    org_id: Optional[UUID],
    session_id: Optional[UUID],
    limit: int,
    offset: int,
    include_main: bool = True,
) -> Dict[str, Any]:
    """
    Return a paginated list of documents the user can access.
    Includes org/personal documents (storage_nodes) and session documents.

    ``include_main`` mirrors ``search_documents``' rag_enabled gate: with RAG
    off, only session documents are visible. Listing org files the search tool
    cannot read is what makes the model claim it lacks access to them.
    """
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    docs: List[dict] = []

    # ── Org / personal documents ──────────────────────────────────────────
    try:
        if not include_main:
            query = None
        elif org_id:
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
                    "source": "organization" if org_id else "personal",
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
    query: str,
    limit: int = 10,
    include_main: bool = True,
) -> Dict[str, Any]:
    """Search storage_nodes by filename, description, or tags (metadata only).

    ``include_main`` gates org/personal documents — see
    ``_list_accessible_documents``.
    """
    limit = max(1, min(limit, 20))
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
        if org_id:
            return q.eq("org_id", str(org_id))
        return q.eq("owner_id", str(user_id)).is_("org_id", "null")

    metadata_columns = ("name", "description") if include_main else ()

    try:
        for col in metadata_columns:
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
                        "source": "organization" if org_id else "personal",
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
    document_name: str,
    max_chunks: int = 40,
    include_main: bool = True,
) -> Dict[str, Any]:
    """Retrieve all text chunks for a named document from Qdrant.

    ``include_main`` gates org/personal documents — see
    ``_list_accessible_documents``.
    """
    from src.core.qdrant_client import qdrant_client

    max_chunks = max(1, min(max_chunks, 100))

    # Locate document in storage_nodes by partial name match
    try:
        matches = []
        if include_main:
            q = (
                db.admin.table("storage_nodes")
                .select("id, name, file_extension")
                .ilike("name", f"%{document_name}%")
                .eq("status", "active")
                .eq("node_type", "file")
            )
            if org_id:
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
    if org_id:
        accessible = await RAGService.get_accessible_documents_for_rag(user_id, org_id, [doc_id])
        if not accessible:
            return {
                "error": f"Access denied to '{real_name}'",
                "chunks": [],
                "document_name": real_name,
            }

    namespace = str(org_id) if org_id else str(user_id)
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


def _sources_for_check(
    sources: List[dict],
    document_contents: Optional[List[dict]],
) -> List[dict]:
    """Give the hallucination check the full text sitting behind each source.

    ``assemble_sources`` keeps only a short preview for documents that were read
    in full — plenty for the UI, but checking an answer against a preview of the
    very document it was written from reports the rest of the answer as
    unsupported. The full text goes onto a throwaway copy, so it never reaches
    the websocket payload or the persisted message row.
    """
    if not document_contents:
        return sources

    full_by_doc = {
        dc["document_id"]: "\n\n".join(
            c.get("chunk_text", "") for c in (dc.get("chunks") or [])
        )
        for dc in document_contents
        if dc.get("document_id") and not dc.get("error")
    }
    if not full_by_doc:
        return sources

    return [
        {**s, "full_text": full_by_doc[s["document_id"]]}
        if s.get("kind") == "document" and s.get("document_id") in full_by_doc
        else s
        for s in sources
    ]


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
    def assemble_sources(
        rag_results: List[dict],
        document_contents: Optional[List[dict]] = None,
        web_results: Optional[List[dict]] = None,
        default_source_type: str = "organization",
    ) -> List[dict]:
        """Build the citable source list, numbered from 1.

        This is the single place citation numbers are assigned; ``build_context``
        renders from the same list, so the ``[n]`` markers the model sees always
        line up with the sources the user can click.
        """
        sources: List[dict] = []

        for r in rag_results:
            sources.append({
                "kind": "rag",
                "document_id": r.get("document_id"),
                "document_name": r.get("document_name", "Unknown"),
                "chunk_index": r.get("chunk_index", 0),
                "chunk_text": r.get("chunk_text", ""),
                "section_header": r.get("section_header", ""),
                "page_numbers": r.get("page_numbers", []),
                # Prefer the cross-encoder score — it is a calibrated relevance
                # value, unlike a raw cosine or RRF score
                "score": r.get("rerank_score", r.get("score", 0.0)),
                "source_type": r.get("source") or default_source_type,
            })

        # Documents read in full are sources too — without this, answers built
        # from get_document_content show an empty Sources panel.
        cited_doc_ids = {s["document_id"] for s in sources}
        for dc in (document_contents or []):
            doc_id = dc.get("document_id")
            chunks = dc.get("chunks") or []
            if dc.get("error") or not doc_id or doc_id in cited_doc_ids or not chunks:
                continue
            cited_doc_ids.add(doc_id)
            preview = " ".join(c.get("chunk_text", "") for c in chunks[:2])
            sources.append({
                "kind": "document",
                "document_id": doc_id,
                "document_name": dc.get("document_name", "Unknown"),
                "chunk_index": 0,
                "chunk_text": preview[:1000],
                "section_header": "",
                "page_numbers": [],
                "score": 1.0,
                "source_type": dc.get("source") or default_source_type,
            })

        for w in (web_results or []):
            sources.append({
                "kind": "web",
                "title": w.get("title", ""),
                "url": w.get("url", ""),
                "snippet": w.get("snippet", ""),
                "source_type": "web",
            })

        for i, source in enumerate(sources, 1):
            source["citation"] = i

        return sources

    @staticmethod
    def build_context(
        rag_results: List[dict],
        web_results: Optional[List[dict]] = None,
        document_contents: Optional[List[dict]] = None,
        calculation_results: Optional[List[dict]] = None,
        document_listings: Optional[List[dict]] = None,
        max_tokens: Optional[int] = None,
        sources: Optional[List[dict]] = None,
    ) -> str:
        """Build the context string from all tool results, within a token budget.

        Sections are filled in priority order; whatever no longer fits is
        dropped with an explicit note so the model knows the view is partial
        rather than silently answering from a truncated document.
        """
        budget = max_tokens or settings.RAG_MAX_CONTEXT_LENGTH
        parts: List[str] = []
        used = 0

        # Citation numbers come from the assembled source list so the markers in
        # the answer resolve to the sources shown in the UI.
        sources = sources if sources is not None else RAGGenerationMixin.assemble_sources(
            rag_results, document_contents, web_results
        )
        rag_citations = [s["citation"] for s in sources if s.get("kind") == "rag"]
        doc_citations = {
            s["document_id"]: s["citation"] for s in sources if s.get("kind") == "document"
        }
        web_citations = [s["citation"] for s in sources if s.get("kind") == "web"]

        def add(text: str) -> bool:
            """Append *text* if it fits the remaining budget."""
            nonlocal used
            cost = _token_len(text)
            if used + cost > budget:
                return False
            parts.append(text)
            used += cost
            return True

        def note_truncation(what: str, omitted: int):
            # Appended unconditionally: the model must always know its view is
            # partial, even when the budget is exactly exhausted.
            parts.append(f"\n_[{omitted} {what} omitted — context budget reached]_\n")

        # ── Calculations (tiny, always first) ─────────────────────────────
        if calculation_results:
            add("\n## Calculation Results:\n")
            for c in calculation_results:
                if c.get("error"):
                    add(f"- {c['expression']} → Error: {c['error']}\n")
                else:
                    add(f"- {c['expression']} = {c['formatted']}\n")

        # ── Available documents (from list_documents / find_document_by_name) ──
        if document_listings:
            seen_docs: set = set()
            listed: List[str] = []
            for entry in document_listings:
                for d in entry.get("documents", []):
                    key = d.get("id")
                    if key in seen_docs:
                        continue
                    seen_docs.add(key)
                    line = f"- {d.get('name', 'Unknown')}"
                    if d.get("type"):
                        line += f" ({d['type']})"
                    if d.get("source"):
                        line += f" [{d['source']}]"
                    if d.get("description"):
                        line += f" — {d['description']}"
                    listed.append(line + "\n")

            if listed:
                add("\n## Available Documents:\n")
                omitted = 0
                for line in listed:
                    if not add(line):
                        omitted += 1
                if omitted:
                    note_truncation("documents", omitted)
            else:
                add("\n## Available Documents:\nNo matching documents were found.\n")

        # ── Retrieved excerpts ────────────────────────────────────────────
        if rag_results:
            add("## Relevant Document Excerpts:\n")
            omitted = 0
            for i, r in zip(rag_citations, rag_results):
                header = f"### Source {i}: {r['document_name']}"
                section = r.get("section_header")
                pages = r.get("page_numbers")
                if section:
                    header += f" — {section}"
                if pages:
                    page_str = ", ".join(str(p) for p in pages)
                    header += f" (p. {page_str})"
                if not add(f"\n{header}\n{r['chunk_text']}\n"):
                    omitted += 1
            if omitted:
                note_truncation("excerpts", omitted)

        # ── Web results ───────────────────────────────────────────────────
        if web_results:
            add("\n## Web Search Results:\n")
            omitted = 0
            for i, r in zip(web_citations, web_results):
                if not add(f"\n### Source {i}: {r['title']}\nURL: {r['url']}\n{r['snippet']}\n"):
                    omitted += 1
            if omitted:
                note_truncation("web results", omitted)

        # ── Full document reads (largest; gets the remaining budget) ──────
        if document_contents:
            add("\n## Full Document Contents:\n")
            for dc in document_contents:
                if dc.get("error"):
                    add(f"\n### {dc.get('document_name', 'Unknown')}\nNote: {dc['error']}\n")
                    continue
                number = doc_citations.get(dc.get("document_id"))
                label = f"Source {number}: " if number else ""
                add(f"\n### {label}{dc['document_name']}\n")
                omitted = 0
                for chunk in dc.get("chunks", []):
                    if not add(chunk["chunk_text"] + "\n"):
                        omitted += 1
                if omitted:
                    note_truncation(f"chunks of '{dc['document_name']}'", omitted)

        return "".join(parts)

    @staticmethod
    async def generate_response(
        user_message: str,
        session_id: UUID,
        user_id: UUID,
        org_id: Optional[UUID],
        rag_enabled: bool = True,
        web_search_enabled: bool = False,
        selected_node_ids: Optional[List[str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Agentic RAG pipeline. Yields dicts with keys:
          type: tool_start | tool_done | rag_context | web_search |
                reasoning | chunk | done | error
        """
        client = openai_client.client

        # Expand selected files/folders into a flat set of document IDs to scope
        # retrieval. Empty means search the whole namespace.
        selected_document_ids = await RAGService.resolve_selected_document_ids(
            selected_node_ids
        ) if selected_node_ids else None
        all_rag_results: List[dict] = []
        all_web_results: List[dict] = []
        all_document_contents: List[dict] = []
        all_calculation_results: List[dict] = []
        all_document_listings: List[dict] = []
        # Largest top_k any search asked for — the reranker must not cut below it
        max_requested_top_k = 0
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

            seen_keys: set = set()
            seen_calls: set = set()
            searched_queries: set = set()
            used_tools = False

            # Tool calling runs for a bounded number of rounds, feeding results
            # back each time so the model can react to them — retry a search
            # that came back empty, or stop early once it has enough.
            for tool_round in range(settings.RAG_MAX_TOOL_ROUNDS if available_tools else 0):
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

                # Skip calls already made in an earlier round — without this the
                # model can loop on the same query and burn the round budget.
                fresh_calls = []
                for call in function_calls:
                    key = (call.name, call.arguments)
                    if key in seen_calls:
                        continue
                    seen_calls.add(key)
                    fresh_calls.append(call)

                if not fresh_calls:
                    break

                used_tools = True
                round_outputs: List[dict] = []

                for call in fresh_calls:
                    try:
                        args = json.loads(call.arguments)
                    except (TypeError, ValueError):
                        args = {}
                    tool_summary: Dict[str, Any] = {"status": "ok"}

                    # ── search_documents ──────────────────────────────────────
                    if call.name == "search_documents":
                        query = args.get("query") or user_message
                        top_k = max(3, min(int(args.get("top_k", 8)), 20))
                        max_requested_top_k = max(max_requested_top_k, top_k)
                        fetch_k = top_k * settings.RAG_RETRIEVAL_TOP_K_MULTIPLIER
                        searched_queries.add(query)

                        yield {"type": "tool_start", "name": "search_documents",
                               "query": query, "top_k": top_k}

                        try:
                            results = await RAGService.search_documents(
                                query=query,
                                user_id=user_id,
                                org_id=org_id,
                                session_id=session_id,
                                top_k=fetch_k,
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

                        tool_summary = {
                            "query": query,
                            "match_count": len(results),
                            "matches": [
                                {
                                    "document": r["document_name"],
                                    "section": r.get("section_header", ""),
                                    "preview": (r.get("chunk_text") or "")[:180],
                                }
                                for r in results[:5]
                            ],
                        }
                        if not results:
                            tool_summary["note"] = (
                                "No matches. Try different wording or a filename."
                            )

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
                                limit=limit,
                                offset=offset,
                                include_main=rag_enabled,
                            )
                        except Exception as e:
                            logger.error("list_documents failed: %s", e)
                            listing = {"documents": [], "count": 0, "limit": limit, "offset": offset}

                        all_document_listings.append(listing)
                        tool_summary = {
                            "count": listing["count"],
                            "documents": [d["name"] for d in listing["documents"]],
                        }
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

                        tool_summary = {
                            "query": query,
                            "count": len(web_results),
                            "titles": [r.get("title", "") for r in web_results[:5]],
                        }
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
                                query=name_query,
                                limit=limit,
                                include_main=rag_enabled,
                            )
                        except Exception as e:
                            logger.error("find_document_by_name failed: %s", e)
                            name_results = {"documents": [], "count": 0, "query": name_query}

                        all_document_listings.append(name_results)
                        tool_summary = {
                            "query": name_query,
                            "count": name_results["count"],
                            "documents": [d["name"] for d in name_results["documents"]],
                        }
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
                                document_name=doc_name,
                                max_chunks=max_chunks,
                                include_main=rag_enabled,
                            )
                        except Exception as e:
                            logger.error("get_document_content failed: %s", e)
                            doc_content = {"error": str(e), "chunks": [], "document_name": doc_name}

                        all_document_contents.append(doc_content)
                        tool_summary = {
                            "document_name": doc_content.get("document_name", doc_name),
                            "chunks_retrieved": len(doc_content.get("chunks", [])),
                        }
                        if doc_content.get("error"):
                            tool_summary["error"] = doc_content["error"]

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
                        tool_summary = calc_result

                        yield {"type": "tool_done", "name": "calculate",
                               "result": calc_result.get("formatted") or calc_result.get("error", "")}

                    # Hand the outcome back so the next round can react to it
                    round_outputs.append({
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(tool_summary, default=str)[:4000],
                    })

                # Record this round's calls and their results in the transcript
                input_messages.extend([
                    {
                        "type": "function_call",
                        "call_id": c.call_id,
                        "name": c.name,
                        "arguments": c.arguments,
                    }
                    for c in fresh_calls
                ])
                input_messages.extend(round_outputs)

            # ── Phase 1b: Retrieval fallback ──────────────────────────────────
            # The router is free to skip retrieval, and on follow-up questions it
            # regularly does: chat history carries no record of earlier tool
            # calls, so "answerable from the conversation" looks true even when
            # the answer is in a document. The turn then generates from an empty
            # context and tells the user nothing was found in their documents —
            # while the very same question works on a retry. If the turn ended
            # with no document content at all, search once on the raw question.
            # When it really was chit-chat the rerank floor drops the results, so
            # the cost is a single query.
            if (
                _SEARCH_DOCUMENTS_TOOL in available_tools
                and not all_rag_results
                and not all_document_contents
                and user_message not in searched_queries
            ):
                logger.info("No document retrieval this turn — running fallback search")
                top_k = settings.RAG_TOP_K
                max_requested_top_k = max(max_requested_top_k, top_k)

                yield {"type": "tool_start", "name": "search_documents",
                       "query": user_message, "top_k": top_k}

                try:
                    results = await RAGService.search_documents(
                        query=user_message,
                        user_id=user_id,
                        org_id=org_id,
                        session_id=session_id,
                        top_k=top_k * settings.RAG_RETRIEVAL_TOP_K_MULTIPLIER,
                        search_main=rag_enabled,
                        search_session=True,
                        selected_document_ids=selected_document_ids,
                    )
                except Exception as e:
                    logger.error("fallback search_documents failed: %s", e)
                    results = []

                for r in results:
                    key = (r["document_id"], r["chunk_index"])
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_rag_results.append(r)

                yield {"type": "tool_done", "name": "search_documents",
                       "count": len(results)}

            # ── Phase 2: Reranking ────────────────────────────────────────────
            if all_rag_results:
                # Keep at least what the model asked for; multiple searches with
                # different angles must not collapse to a single query's budget.
                rerank_top_k = max(settings.RAG_TOP_K, max_requested_top_k)
                all_rag_results = reranker.rerank(
                    query=user_message,
                    chunks=all_rag_results,
                    top_k=rerank_top_k,
                    min_score=settings.RAG_MIN_SCORE,
                    rerank_min_score=settings.RAG_RERANK_MIN_SCORE,
                )

                # Widen the strongest hits with their adjacent chunks — a 512
                # token window often clips the sentence that answers the question
                all_rag_results = await RAGService.expand_with_neighbors(
                    all_rag_results, user_id=user_id, org_id=org_id
                )

            # Always emit rag_context (even empty) so frontend clears tool indicator
            yield {"type": "rag_context", "data": all_rag_results}

            if all_web_results:
                yield {"type": "web_search", "data": all_web_results}

            # ── Phase 3: Streaming Generation ────────────────────────────────
            default_source_type = "organization" if org_id else "personal"

            # Sources are numbered first so the context labels and the citation
            # markers in the answer refer to the same things.
            sources = RAGGenerationMixin.assemble_sources(
                all_rag_results,
                all_document_contents,
                all_web_results,
                default_source_type,
            )

            context = RAGGenerationMixin.build_context(
                all_rag_results,
                all_web_results,
                all_document_contents or None,
                all_calculation_results or None,
                all_document_listings or None,
                sources=sources,
            )

            # `instructions` is the cacheable prefix, so it stays static — the
            # per-turn context goes at the *end* of the input instead. Putting a
            # different context blob in the prefix on every turn would defeat
            # prompt caching entirely.
            gen_input = list(input_messages)
            if context:
                gen_input.append({
                    "role": "developer",
                    "content": (
                        "Context retrieved for the user's latest question. Cite it "
                        f"with the given Source numbers:\n\n{context}"
                    ),
                })

            gen_kwargs: Dict[str, Any] = {}
            if used_tools:
                # The transcript now contains function calls, so the tool
                # definitions must come along; tool_choice="none" stops the model
                # calling anything further at answer time.
                gen_kwargs["tools"] = available_tools
                gen_kwargs["tool_choice"] = "none"

            gen_stream = await client.responses.create(
                model=settings.OPENAI_CHAT_MODEL,
                instructions=settings.RAG_SYSTEM_PROMPT,
                input=gen_input,
                reasoning={"effort": settings.RAG_REASONING_EFFORT, "summary": "detailed"},
                max_output_tokens=settings.OPENAI_CHAT_MAX_TOKENS,
                stream=True,
                **gen_kwargs,
            )

            full_response: List[str] = []
            incomplete_reason: Optional[str] = None
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
                elif event.type == "response.incomplete":
                    # Most often the output token cap — the answer is cut short
                    details = getattr(event.response, "incomplete_details", None)
                    incomplete_reason = getattr(details, "reason", None) or "incomplete"
                    usage = getattr(event.response, "usage", None)
                    if usage:
                        total_prompt_tokens += usage.input_tokens
                        total_completion_tokens += usage.output_tokens
                    logger.warning("Generation incomplete: %s", incomplete_reason)
                elif event.type in ("response.failed", "error"):
                    error = getattr(getattr(event, "response", None), "error", None)
                    message = getattr(error, "message", None) or "generation failed"
                    logger.error("Generation stream failed: %s", message)
                    yield {"type": "error", "error": message}
                    return

            answer = "".join(full_response)
            # Drop markers pointing at sources that do not exist — an
            # unresolvable citation is worse than none.
            answer = strip_invalid_citations(answer, len(sources))

            # ── Phase 4: Hallucination check ──────────────────────────────────
            # Detection only. The verdict travels alongside the answer; it is
            # never applied to it, and the source list is left alone. Because
            # nothing downstream depends on the outcome, this runs on every
            # answer that had sources — no length floor, truncated answers
            # included. Fails open — see judge.judge_answer.
            verdict: Optional[str] = None
            issues: List[dict] = []
            if sources:
                yield {"type": "verifying"}
                judged = await judge_answer(
                    question=user_message,
                    answer=answer,
                    sources=_sources_for_check(sources, all_document_contents),
                )
                verdict = judged["verdict"]
                issues = judged["issues"]
                total_prompt_tokens += judged["prompt_tokens"]
                total_completion_tokens += judged["completion_tokens"]

            if incomplete_reason == "max_output_tokens":
                answer += "\n\n_[Response truncated — it reached the output length limit.]_"

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
                "content": answer,
                "rag_results": all_rag_results,
                "web_results": all_web_results,
                "sources": sources,
                "verdict": verdict,
                "issues": issues,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
            }

        except Exception as e:
            logger.error("RAG generation error: %s", e, exc_info=True)
            yield {"type": "error", "error": str(e)}
