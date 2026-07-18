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

from src.bounce_board import events
from src.bounce_board.constants import PIPELINE_STAGES
from src.core.database import db

logger = logging.getLogger(__name__)

TABLE = "bounce_board_sessions"

# A "running" session whose runner died (server restart) is stale after this.
# Generous: the pipeline touches the row at least once per stage / framework /
# board message, so a healthy run never goes quiet anywhere near this long.
STALE_RUNNING_AFTER = timedelta(minutes=6)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fresh_stages() -> list[dict]:
    return [{"id": s, "status": "pending"} for s in PIPELINE_STAGES]


# Which JSONB artifacts each stage produces — cleared when (re)running it.
STAGE_ARTIFACTS: dict[str, list[str]] = {
    "intake": [],
    "context_detection": ["context"],
    "agent_routing": ["board", "routed_agent_id"],
    "knowledge_retrieval": ["retrieved_sources"],
    "framework_analysis": ["frameworks"],
    "board_discussion": ["discussion"],
    "decision_ranking": ["recommendations"],
    "critique": [],
    "report_generation": ["report"],
    "complete": [],
}


def reset_patch(row: dict, from_stage: Optional[str] = None) -> tuple[dict, str]:
    """Build the update patch that prepares a session for a (re)run.

    With a valid ``from_stage`` (and all earlier stages complete) only the
    stages from that point are reset and their artifacts cleared; otherwise a
    full clean rerun is prepared. Attachments are user uploads, never cleared.
    Returns (patch, effective_from_stage).
    """
    existing_ids = [s.get("id") for s in (row.get("stages") or [])]
    can_resume = (
        from_stage in PIPELINE_STAGES
        and from_stage not in ("intake", "complete")
        and existing_ids == PIPELINE_STAGES
        and all(
            s.get("status") == "complete"
            for s in row["stages"]
            if PIPELINE_STAGES.index(s["id"]) < PIPELINE_STAGES.index(from_stage)
        )
    )
    if can_resume:
        resume_idx = PIPELINE_STAGES.index(from_stage)
        stages = [
            s if PIPELINE_STAGES.index(s["id"]) < resume_idx else {"id": s["id"], "status": "pending"}
            for s in row["stages"]
        ]
        patch: dict = {"status": "running", "current_stage": from_stage, "stages": stages}
        for stage_id in PIPELINE_STAGES[resume_idx:]:
            for artifact in STAGE_ARTIFACTS.get(stage_id, []):
                patch[artifact] = None
        return patch, from_stage
    return (
        {
            "status": "running",
            "current_stage": "intake",
            "stages": fresh_stages(),
            "context": None,
            "routed_agent_id": None,
            "board": None,
            "retrieved_sources": None,
            "frameworks": None,
            "discussion": None,
            "recommendations": None,
            "report": None,
        },
        "intake",
    )


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
    # Attachment text stays server-side; the client only needs the metadata.
    attachments = [
        {k: v for k, v in a.items() if k != "text"} for a in (row.get("attachments") or [])
    ] or None
    return {
        **session_to_summary(row),
        "input": row.get("input") or {},
        "stages": row.get("stages") or fresh_stages(),
        "context": row.get("context"),
        "routedAgentId": row.get("routed_agent_id"),
        "board": row.get("board"),
        "attachments": attachments,
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
        {**s, "status": "error", "detail": "Interrupted by a server restart — retry from this stage."}
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
    row = result.data[0]
    # Push the new state to any live WebSocket subscribers. The session event
    # carries the full API-shaped session; discussion updates get their own
    # event so the board debate streams message-by-message.
    events.publish(session_id, {"type": "session", "session": session_to_api(row)})
    if "discussion" in patch and patch["discussion"] is not None:
        events.publish(session_id, {"type": "discussion", "discussion": patch["discussion"]})
    if "copilot_chat" in patch and patch["copilot_chat"] is not None:
        events.publish(session_id, {"type": "chat", "chat": patch["copilot_chat"]})
    return row


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
