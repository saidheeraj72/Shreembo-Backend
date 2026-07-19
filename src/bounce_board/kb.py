"""
Bounce Board — issue knowledge base.

Rows live in Postgres (bounce_board_kb_issues); embeddings live in Qdrant
collection `bounce-board-kb` under the single shared namespace `bb-kb`
(industry / source_type / org_id are payload filters, not namespaces, so the
seeded global KB and org uploads are searched together).
"""
import asyncio
import logging
from typing import Optional

from fastapi import HTTPException

from src.bounce_board.constants import (
    AGENT_BY_INDUSTRY,
    KB_COLLECTION,
    KB_NAMESPACE,
    SOURCE_TYPE_LABELS,
)
from src.core.database import db
from src.core.openai_client import openai_client
from src.core.qdrant_client import qdrant_client

logger = logging.getLogger(__name__)

TABLE = "bounce_board_kb_issues"


def issue_to_api(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "industry": row["industry"],
        "sourceType": row["source_type"],
        "severity": row["severity"],
        "tags": row.get("tags") or [],
        "summary": row.get("summary") or "",
        "contentMd": row.get("content_md") or "",
        "relatedIssueIds": row.get("related_issue_ids") or [],
        "addedAt": row["created_at"],
        "timesReferenced": row.get("times_referenced") or 0,
    }


def list_issues(
    industry: Optional[str] = None,
    source_type: Optional[str] = None,
    search: Optional[str] = None,
    org_id: Optional[str] = None,
) -> list[dict]:
    """List KB issues visible to the caller: global entries (org_id NULL) plus
    the caller's own org's entries — never another org's uploads."""
    q = db.admin.table(TABLE).select("*").order("created_at", desc=True)
    if org_id:
        q = q.or_(f"org_id.is.null,org_id.eq.{org_id}")
    else:
        q = q.is_("org_id", "null")
    if industry:
        q = q.eq("industry", industry)
    if source_type:
        q = q.eq("source_type", source_type)
    rows = q.execute().data or []
    if search:
        needle = search.lower()
        rows = [
            r
            for r in rows
            if needle in (r.get("title") or "").lower()
            or needle in (r.get("summary") or "").lower()
            or any(needle in str(t).lower() for t in (r.get("tags") or []))
        ]
    return [issue_to_api(r) for r in rows]


def get_issue(issue_id: str, org_id: Optional[str] = None) -> dict:
    result = db.admin.table(TABLE).select("*").eq("id", issue_id).maybe_single().execute()
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Knowledge base entry not found")
    row_org = result.data.get("org_id")
    if row_org and row_org != org_id:
        raise HTTPException(status_code=404, detail="Knowledge base entry not found")
    return issue_to_api(result.data)


def count_issues(org_id: Optional[str] = None) -> int:
    q = db.admin.table(TABLE).select("id", count="exact")
    if org_id:
        q = q.or_(f"org_id.is.null,org_id.eq.{org_id}")
    else:
        q = q.is_("org_id", "null")
    result = q.execute()
    return result.count or 0


async def create_issue(
    *,
    title: str,
    industry: str,
    source_type: str,
    severity: str = "medium",
    summary: str = "",
    content_md: str = "",
    tags: Optional[list[str]] = None,
    related_issue_ids: Optional[list[str]] = None,
    org_id: Optional[str] = None,
    issue_id: Optional[str] = None,
) -> dict:
    """Insert a KB issue row and embed it into Qdrant."""
    row = {
        "title": title,
        "industry": industry,
        "source_type": source_type,
        "severity": severity,
        "summary": summary,
        "content_md": content_md,
        "tags": tags or [],
        "related_issue_ids": related_issue_ids or [],
        "org_id": org_id,
    }
    if issue_id:
        row["id"] = issue_id
    result = db.admin.table(TABLE).upsert(row, on_conflict="id").execute()
    saved = result.data[0]
    await embed_issue(saved)
    return issue_to_api(saved)


async def embed_issue(row: dict) -> None:
    text = f"{row['title']}\n\n{row.get('summary') or ''}\n\n{row.get('content_md') or ''}"
    vector = await openai_client.get_embedding(text)
    await qdrant_client.upsert(
        vectors=[
            {
                "id": f"bb-kb-{row['id']}",
                "values": vector,
                "metadata": {
                    "kb_issue_id": row["id"],
                    "title": row["title"],
                    "industry": row["industry"],
                    "source_type": row["source_type"],
                    "severity": row["severity"],
                    "excerpt": (row.get("summary") or "")[:300],
                    "org_id": row.get("org_id"),
                },
            }
        ],
        namespace=KB_NAMESPACE,
        index_name=KB_COLLECTION,
    )


# Matches scoring below this are noise — citing them would be false grounding.
MIN_RELEVANCE = 0.25
# How much of each source's full content is handed to the LLM stages.
_CONTENT_CHARS = 2_000


async def search(
    query_text: str,
    industry: Optional[str] = None,
    top_k: int = 5,
    org_id: Optional[str] = None,
) -> list[dict]:
    """Semantic search over the KB; returns RetrievedSource dicts (camelCase).

    Only global entries (org_id NULL) and the caller org's entries are returned.
    Each source carries `contentMd` (capped) for LLM grounding — the API models
    drop it on serialization so it never bloats client payloads.
    """
    vector = await openai_client.get_embedding(query_text)
    # Over-fetch so the org post-filter and relevance floor still leave top_k.
    matches = await qdrant_client.query(
        vector=vector,
        namespace=KB_NAMESPACE,
        top_k=top_k * 4,
        filter={"industry": industry} if industry else None,
        index_name=KB_COLLECTION,
    )
    sources = []
    for m in matches:
        if not m.metadata.get("kb_issue_id"):
            continue
        match_org = m.metadata.get("org_id")
        if match_org and match_org != org_id:
            continue
        score = max(0.0, min(1.0, float(m.score)))
        if score < MIN_RELEVANCE:
            continue
        sources.append(
            {
                "kbIssueId": m.metadata.get("kb_issue_id"),
                "title": m.metadata.get("title") or "",
                "sourceType": m.metadata.get("source_type") or "company_doc",
                "relevance": round(score, 3),
                "excerpt": m.metadata.get("excerpt") or "",
            }
        )
        if len(sources) >= top_k:
            break
    await asyncio.to_thread(_enrich_with_content, sources)
    await asyncio.to_thread(_bump_reference_counts, [s["kbIssueId"] for s in sources])
    return sources


def _enrich_with_content(sources: list[dict]) -> None:
    """Attach each source's full content_md (capped) for downstream prompts."""
    if not sources:
        return
    try:
        rows = (
            db.admin.table(TABLE)
            .select("id, content_md")
            .in_("id", [s["kbIssueId"] for s in sources])
            .execute()
        ).data or []
        content_by_id = {r["id"]: r.get("content_md") or "" for r in rows}
        for s in sources:
            s["contentMd"] = content_by_id.get(s["kbIssueId"], "")[:_CONTENT_CHARS]
    except Exception:  # noqa: BLE001 — grounding depth is best-effort
        logger.warning("Failed to enrich KB sources with content", exc_info=True)


def _bump_reference_counts(issue_ids: list[str]) -> None:
    if not issue_ids:
        return
    try:
        rows = (
            db.admin.table(TABLE)
            .select("id, times_referenced")
            .in_("id", issue_ids)
            .execute()
        ).data or []
        for row in rows:
            db.admin.table(TABLE).update(
                {"times_referenced": (row.get("times_referenced") or 0) + 1}
            ).eq("id", row["id"]).execute()
    except Exception:  # noqa: BLE001 — counters must never break retrieval
        logger.debug("Failed to bump reference counts for %s", issue_ids)


def mind_map(industry: Optional[str] = None, org_id: Optional[str] = None) -> dict:
    """Industry → source-type category → issue graph (same shape as the mock)."""
    rows = list_issues(industry=industry, org_id=org_id)
    nodes: list[dict] = []
    edges: list[dict] = []
    industries = sorted({r["industry"] for r in rows})
    for ind in industries:
        ind_node = f"industry-{ind}"
        agent = AGENT_BY_INDUSTRY.get(ind)
        nodes.append({"id": ind_node, "label": agent["name"] if agent else ind, "type": "industry"})
        ind_rows = [r for r in rows if r["industry"] == ind]
        for st in sorted({r["sourceType"] for r in ind_rows}):
            cat_node = f"{ind_node}-{st}"
            nodes.append({"id": cat_node, "label": SOURCE_TYPE_LABELS.get(st, st), "type": "category"})
            edges.append({"id": f"e-{ind_node}-{cat_node}", "source": ind_node, "target": cat_node})
            for issue in (r for r in ind_rows if r["sourceType"] == st):
                nodes.append(
                    {
                        "id": issue["id"],
                        "label": issue["title"],
                        "type": "issue",
                        "severity": issue["severity"],
                        "kbIssueId": issue["id"],
                    }
                )
                edges.append({"id": f"e-{cat_node}-{issue['id']}", "source": cat_node, "target": issue["id"]})
    return {"nodes": nodes, "edges": edges}
