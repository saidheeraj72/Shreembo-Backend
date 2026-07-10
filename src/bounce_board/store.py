"""
Bounce Board — Supabase persistence for analysis sessions.

Rows use snake_case columns; the JSONB artifact blobs (input, stages, context,
retrieved_sources, frameworks, discussion, recommendations, report) are stored
in the camelCase shape of the frontend contract so they can be served back
verbatim.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException

from src.bounce_board.constants import PIPELINE_STAGES
from src.core.database import db

logger = logging.getLogger(__name__)

TABLE = "bounce_board_sessions"

# A "running" session whose runner died (server restart) is stale after this.
STALE_RUNNING_AFTER = timedelta(minutes=2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fresh_stages() -> list[dict]:
    return [{"id": s, "status": "pending"} for s in PIPELINE_STAGES]


def _excerpt(text: str, length: int = 140) -> str:
    text = (text or "").strip()
    return text[:length].rstrip() + "…" if len(text) > length else text


def session_to_summary(row: dict) -> dict:
    stages = row.get("stages") or []
    context = row.get("context") or {}
    input_ = row.get("input") or {}
    industry = context.get("industry") or (
        input_.get("industryHint") if not input_.get("autoDetect", True) else None
    )
    return {
        "id": row["id"],
        "title": row["title"],
        "problemExcerpt": row.get("problem_excerpt") or "",
        "status": row["status"],
        "industry": industry,
        "currentStage": row.get("current_stage") or "intake",
        "stagesComplete": sum(1 for s in stages if s.get("status") == "complete"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def session_to_api(row: dict) -> dict:
    return {
        **session_to_summary(row),
        "input": row.get("input") or {},
        "stages": row.get("stages") or fresh_stages(),
        "context": row.get("context"),
        "routedAgentId": row.get("routed_agent_id"),
        "retrievedSources": row.get("retrieved_sources"),
        "frameworks": row.get("frameworks"),
    }


def create_session(user_id: UUID, org_id: Optional[str], input_camel: dict) -> dict:
    title = (input_camel.get("title") or "").strip() or _excerpt(
        input_camel.get("problemStatement", ""), 60
    )
    row = {
        "user_id": str(user_id),
        "org_id": org_id,
        "title": title,
        "problem_excerpt": _excerpt(input_camel.get("problemStatement", "")),
        "status": "draft",
        "current_stage": "intake",
        "stages": fresh_stages(),
        "input": input_camel,
    }
    result = db.admin.table(TABLE).insert(row).execute()
    return result.data[0]


def list_sessions(user_id: UUID) -> list[dict]:
    result = (
        db.admin.table(TABLE)
        .select("id, title, problem_excerpt, status, current_stage, stages, context, input, created_at, updated_at")
        .eq("user_id", str(user_id))
        .order("updated_at", desc=True)
        .execute()
    )
    return result.data or []


def get_session(user_id: UUID, session_id: str) -> dict:
    result = (
        db.admin.table(TABLE)
        .select("*")
        .eq("id", session_id)
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Analysis session not found")
    row = result.data
    row = _recover_if_stale(row)
    return row


def _recover_if_stale(row: dict) -> dict:
    """Flip a session to error if it claims to be running but the runner died."""
    if row.get("status") != "running":
        return row
    try:
        updated = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return row
    if datetime.now(timezone.utc) - updated < STALE_RUNNING_AFTER:
        return row
    stages = [
        {**s, "status": "error", "detail": "Interrupted by a server restart — rerun the analysis."}
        if s.get("status") == "running"
        else s
        for s in (row.get("stages") or [])
    ]
    logger.warning("Recovering stale running session %s -> error", row["id"])
    return update_session(row["id"], {"status": "error", "stages": stages})


def update_session(session_id: str, patch: dict) -> dict:
    result = db.admin.table(TABLE).update(patch).eq("id", session_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Analysis session not found")
    return result.data[0]


def delete_session(user_id: UUID, session_id: str) -> None:
    db.admin.table(TABLE).delete().eq("id", session_id).eq("user_id", str(user_id)).execute()


def stats(user_id: UUID) -> dict:
    rows = (
        db.admin.table(TABLE)
        .select("status, recommendations")
        .eq("user_id", str(user_id))
        .execute()
    ).data or []
    return {
        "totalSessions": len(rows),
        "runningSessions": sum(1 for r in rows if r["status"] == "running"),
        "totalRecommendations": sum(len(r.get("recommendations") or []) for r in rows),
    }
