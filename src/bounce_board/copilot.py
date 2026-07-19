"""
Bounce Board — session copilot.

An AI chat attached to each analysis session. It answers questions grounded in
every artifact (context, board, frameworks, discussion, recommendations,
report) and can MODIFY the analysis via a validated action vocabulary:

  update_problem        rewrite the problem statement (input)
  upsert_kpi            add or update a KPI input by name
  update_recommendation edit a recommendation's fields (kept in report too)
  update_report_section rewrite executiveSummaryMd / managementReportMd
  rerun_from            reset from a pipeline stage and rerun

The conversation lives on the session row (copilot_chat JSONB); every write is
published to the session WebSocket as a {"type": "chat"} event.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from src.bounce_board import events, pipeline, store
from src.bounce_board.constants import PIPELINE_STAGES

logger = logging.getLogger(__name__)

_MAX_CHAT_MESSAGES = 60          # oldest messages beyond this are dropped
_HISTORY_IN_PROMPT = 10

# Fallback storage for deployments where the copilot_chat column has not been
# added yet (migrations/bounce_board_v3.sql). Chat still works, but only
# survives until the process restarts — the warning below says how to fix it.
_memory_chat: dict[str, list[dict]] = {}


def get_history(row: dict) -> list[dict]:
    return row.get("copilot_chat") or _memory_chat.get(row["id"]) or []


def _save_history(session_id: str, messages: list[dict]) -> None:
    messages = messages[-_MAX_CHAT_MESSAGES:]
    try:
        store.update_session(session_id, {"copilot_chat": messages})
        _memory_chat.pop(session_id, None)
    except Exception:  # noqa: BLE001 — missing column must not break the chat
        logger.warning(
            "copilot_chat column missing (run migrations/bounce_board_v3.sql) — "
            "keeping session %s chat in memory only",
            session_id,
        )
        _memory_chat[session_id] = messages
        # The DB write normally publishes to the WebSocket; do it ourselves.
        events.publish(session_id, {"type": "chat", "chat": messages})

_REPORT_SECTIONS = ("executiveSummaryMd", "managementReportMd")
_REC_EDITABLE_TEXT = ("title", "description", "category")
_REC_EDITABLE_NUM = ("estimatedCost", "estimatedRoiPct", "effortWeeks")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_COPILOT_PROMPT = """You are the copilot of an AI "bounce board" business analysis session. You have
the full analysis in the payload. Answer the user's message directly and, when
they ask you to CHANGE something, emit the matching actions.

Available actions (emit only what the user asked for; empty list otherwise):
  {"type": "update_problem", "problemStatement": "full new statement"}
  {"type": "upsert_kpi", "name": "...", "current": <number>, "target": <number>, "unit": "..."}
  {"type": "update_recommendation", "rank": <1-based rank>, "patch": {"title"?, "description"?, "category"?, "estimatedCost"?, "estimatedRoiPct"?, "effortWeeks"?}}
  {"type": "update_report_section", "section": "executiveSummaryMd" | "managementReportMd", "contentMd": "full replacement markdown"}
  {"type": "rerun_from", "stage": "context_detection" | "agent_routing" | "knowledge_retrieval" | "framework_analysis" | "board_discussion" | "decision_ranking" | "critique" | "report_generation"}

Rules:
- Ground every claim in the payload; never invent data. If asked about something
  not in the analysis, say so.
- For edits, produce the COMPLETE new value (full statement / full markdown
  section), not a diff.
- If the user changes the problem or KPIs and wants updated conclusions, also
  emit rerun_from at the right stage (context_detection for problem changes,
  framework_analysis for KPI changes) — but only when they ask to re-analyze.
- Refuse changes that would fabricate numbers; explain what input you need.

Return STRICT JSON:
{"replyMd": "your answer in markdown, 1-6 sentences unless asked for more",
 "actions": [ ...as above, may be empty ]}"""


def _chat_context(row: dict) -> dict:
    """Everything the copilot may ground on, compact enough for one prompt."""
    input_camel = row.get("input") or {}
    board = row.get("board") or {}
    report = row.get("report") or {}
    discussion = (row.get("discussion") or {}).get("messages") or []
    return {
        "sessionStatus": row.get("status"),
        "title": row.get("title"),
        "problemStatement": input_camel.get("problemStatement"),
        "kpis": input_camel.get("kpis") or [],
        "attachments": [a.get("name") for a in (row.get("attachments") or [])],
        "context": row.get("context"),
        "board": {
            "expert": (board.get("expert") or {}).get("name"),
            "personas": [
                {"id": p["id"], "name": p["name"], "title": p["title"]}
                for p in board.get("personas") or []
            ],
        },
        "analysisDigest": pipeline._frameworks_digest(row.get("frameworks") or []),
        "sources": [
            {"title": s.get("title"), "excerpt": s.get("excerpt")}
            for s in row.get("retrieved_sources") or []
        ],
        "recommendations": [
            {
                "rank": r.get("rank"),
                "title": r.get("title"),
                "description": r.get("description"),
                "compositeScore": r.get("compositeScore"),
                "estimatedCost": r.get("estimatedCost"),
                "estimatedRoiPct": r.get("estimatedRoiPct"),
                "effortWeeks": r.get("effortWeeks"),
                "assumptions": r.get("assumptions") or [],
                "critique": r.get("critique"),
            }
            for r in row.get("recommendations") or []
        ],
        "reportExecutiveSummaryMd": report.get("executiveSummaryMd"),
        "reportManagementReportMd": report.get("managementReportMd"),
        "recentBoardMessages": [
            {"agentId": m.get("agentId"), "message": m.get("contentMd")}
            for m in discussion[-6:]
        ],
    }


# ---------------------------------------------------------------------------
# Action execution — each returns a short summary string for the UI chip.
# ---------------------------------------------------------------------------


def _apply_update_problem(row: dict, action: dict) -> str:
    statement = (action.get("problemStatement") or "").strip()
    if len(statement) < 20:
        raise ValueError("New problem statement is too short")
    input_camel = {**(row.get("input") or {}), "problemStatement": statement}
    store.update_session(
        row["id"],
        {"input": input_camel, "problem_excerpt": store._excerpt(statement)},
    )
    row["input"] = input_camel
    return "Updated the problem statement"


def _apply_upsert_kpi(row: dict, action: dict) -> str:
    name = (action.get("name") or "").strip()
    if not name:
        raise ValueError("KPI needs a name")
    kpi = {
        "name": name,
        "current": float(action.get("current") or 0),
        "target": float(action.get("target") or 0),
        "unit": str(action.get("unit") or ""),
    }
    input_camel = {**(row.get("input") or {})}
    kpis = [k for k in (input_camel.get("kpis") or []) if k.get("name") != name]
    kpis.append(kpi)
    input_camel["kpis"] = kpis
    store.update_session(row["id"], {"input": input_camel})
    row["input"] = input_camel
    return f"Set KPI “{name}” to {kpi['current']}{kpi['unit']} → {kpi['target']}{kpi['unit']}"


def _apply_update_recommendation(row: dict, action: dict) -> str:
    recommendations = list(row.get("recommendations") or [])
    if not recommendations:
        raise ValueError("There are no recommendations to edit yet")
    rank = action.get("rank")
    target = next((r for r in recommendations if r.get("rank") == rank), None)
    if target is None:
        raise ValueError(f"No recommendation with rank {rank}")
    patch = action.get("patch") or {}
    changed = []
    for key in _REC_EDITABLE_TEXT:
        if key in patch and str(patch[key]).strip():
            target[key] = str(patch[key]).strip()
            changed.append(key)
    for key in _REC_EDITABLE_NUM:
        if key in patch:
            try:
                target[key] = float(patch[key])
                changed.append(key)
            except (TypeError, ValueError):
                pass
    if not changed:
        raise ValueError("Nothing editable in the requested change")
    updates: dict = {"recommendations": recommendations}
    # The report embeds a copy of the recommendations — keep it consistent.
    report = row.get("report")
    if report and report.get("recommendations"):
        report = {**report, "recommendations": recommendations}
        updates["report"] = report
        row["report"] = report
    store.update_session(row["id"], updates)
    row["recommendations"] = recommendations
    return f"Edited recommendation #{rank} ({', '.join(changed)})"


def _apply_update_report_section(row: dict, action: dict) -> str:
    section = action.get("section")
    content = (action.get("contentMd") or "").strip()
    if section not in _REPORT_SECTIONS:
        raise ValueError(f"Unknown report section '{section}'")
    if not content:
        raise ValueError("Replacement content is empty")
    report = row.get("report")
    if not report:
        raise ValueError("The report has not been generated yet")
    report = {**report, section: content}
    store.update_session(row["id"], {"report": report})
    row["report"] = report
    label = "executive summary" if section == "executiveSummaryMd" else "management report"
    return f"Rewrote the {label}"


def _apply_rerun_from(row: dict, action: dict) -> str:
    stage = action.get("stage")
    if stage not in PIPELINE_STAGES or stage in ("intake", "complete"):
        raise ValueError(f"Cannot rerun from '{stage}'")
    patch, effective = store.reset_patch(row, stage)
    store.update_session(row["id"], patch)
    pipeline.start(row["id"])
    return f"Rerunning the analysis from {effective.replace('_', ' ')}"


_ACTION_HANDLERS = {
    "update_problem": _apply_update_problem,
    "upsert_kpi": _apply_upsert_kpi,
    "update_recommendation": _apply_update_recommendation,
    "update_report_section": _apply_update_report_section,
    "rerun_from": _apply_rerun_from,
}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def _message(role: str, content_md: str, actions: Optional[list[dict]] = None) -> dict:
    return {
        "id": f"chat-{uuid.uuid4().hex[:10]}",
        "role": role,
        "contentMd": content_md,
        "actions": actions or [],
        "timestamp": _now(),
    }


async def chat(row: dict, user_message: str) -> list[dict]:
    """One copilot turn. Appends the user + assistant messages to the session's
    chat, executes any actions, and returns the full updated message list."""
    history = list(get_history(row))
    history.append(_message("user", user_message))
    # Show the user message immediately (also lands on the WebSocket).
    _save_history(row["id"], history)

    running = row.get("status") == "running"
    client = pipeline._client()
    try:
        data = await pipeline._llm_json(
            client,
            _COPILOT_PROMPT,
            {
                "analysis": _chat_context(row),
                "chatHistory": [
                    {"role": m["role"], "message": m["contentMd"]}
                    for m in history[-_HISTORY_IN_PROMPT:-1]
                ],
                "userMessage": user_message,
                "note": "The pipeline is currently RUNNING — actions are disabled; answer only."
                if running
                else None,
            },
        )
    except Exception:  # noqa: BLE001 — the chat must answer, even if only to apologize
        logger.exception("Copilot LLM call failed for session %s", row["id"])
        history.append(
            _message("assistant", "I couldn't process that just now — please try again.")
        )
        _save_history(row["id"], history)
        return history

    executed: list[dict] = []
    reran = False
    if not running:
        for action in (data.get("actions") or [])[:5]:
            handler = _ACTION_HANDLERS.get(action.get("type"))
            if handler is None:
                continue
            if reran:
                # Nothing may mutate artifacts after a rerun has been kicked off.
                executed.append(
                    {"type": action["type"], "summary": "Skipped — a rerun is already in progress", "ok": False}
                )
                continue
            try:
                summary = handler(row, action)
                executed.append({"type": action["type"], "summary": summary, "ok": True})
                if action["type"] == "rerun_from":
                    reran = True
            except Exception as e:  # noqa: BLE001 — a bad action must not kill the reply
                logger.warning("Copilot action %s failed: %s", action.get("type"), e)
                executed.append({"type": action.get("type"), "summary": str(e), "ok": False})
    elif data.get("actions"):
        executed.append(
            {"type": "none", "summary": "Changes are disabled while the pipeline is running", "ok": False}
        )

    history.append(_message("assistant", data.get("replyMd") or "…", executed))
    _save_history(row["id"], history)
    return history
