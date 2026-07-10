"""
Bounce Board — analysis session endpoints.

  GET    /stats                          headline numbers for the dashboard
  GET    /sessions                       list the user's analyses
  POST   /sessions                       create a draft analysis
  GET    /sessions/{id}                  full session (polled while running)
  POST   /sessions/{id}/start           (re)run the pipeline
  DELETE /sessions/{id}                  delete an analysis
  GET    /sessions/{id}/discussion       board discussion (polled while streaming)
  GET    /sessions/{id}/recommendations  ranked recommendations
  GET    /sessions/{id}/report           final report
  GET    /industry-agents                the four industry agents
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from src.bounce_board import kb, pipeline, store
from src.bounce_board.constants import INDUSTRY_AGENTS
from src.core.dependencies import get_current_user_id
from src.models.bounce_board import (
    BounceBoardStats,
    DiscussionState,
    IndustryAgent,
    Recommendation,
    Report,
    Session,
    SessionInput,
    SessionSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/stats", response_model=BounceBoardStats, summary="Dashboard stats")
async def get_stats(user_id: UUID = Depends(get_current_user_id)) -> dict:
    return {**store.stats(user_id), "kbDocuments": kb.count_issues()}


@router.get("/sessions", response_model=list[SessionSummary], summary="List analyses")
async def list_sessions(user_id: UUID = Depends(get_current_user_id)) -> list[dict]:
    return [store.session_to_summary(row) for row in store.list_sessions(user_id)]


@router.post("/sessions", response_model=Session, summary="Create a draft analysis")
async def create_session(
    input: SessionInput,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    row = store.create_session(user_id, None, input.model_dump(by_alias=True))
    return store.session_to_api(row)


@router.get("/sessions/{session_id}", response_model=Session, summary="Get a full session")
async def get_session(
    session_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    return store.session_to_api(store.get_session(user_id, session_id))


@router.post("/sessions/{session_id}/start", summary="(Re)run the analysis pipeline")
async def start_session(
    session_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    # Return JSON (not 204): the frontend fetch wrapper always parses a body.
    row = store.get_session(user_id, session_id)
    if row["status"] == "running":
        return {"status": "already_running"}
    # Reset all artifacts for a clean (re)run.
    store.update_session(
        session_id,
        {
            "status": "running",
            "current_stage": "intake",
            "stages": store.fresh_stages(),
            "context": None,
            "routed_agent_id": None,
            "retrieved_sources": None,
            "frameworks": None,
            "discussion": None,
            "recommendations": None,
            "report": None,
        },
    )
    pipeline.start(session_id)
    return {"status": "started"}


@router.delete("/sessions/{session_id}", summary="Delete an analysis")
async def delete_session(
    session_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    store.delete_session(user_id, session_id)
    return {"status": "deleted"}


@router.get(
    "/sessions/{session_id}/discussion",
    response_model=DiscussionState,
    summary="Board discussion (polled while streaming)",
)
async def get_discussion(
    session_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    row = store.get_session(user_id, session_id)
    return row.get("discussion") or {"status": "idle", "activeAgentId": None, "messages": []}


@router.get(
    "/sessions/{session_id}/recommendations",
    response_model=list[Recommendation],
    summary="Ranked recommendations",
)
async def get_recommendations(
    session_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> list[dict]:
    row = store.get_session(user_id, session_id)
    return row.get("recommendations") or []


@router.get("/sessions/{session_id}/report", response_model=Report, summary="Final report")
async def get_report(
    session_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    row = store.get_session(user_id, session_id)
    report = row.get("report")
    if not report:
        raise HTTPException(status_code=404, detail="Report not generated yet")
    return report


@router.get("/industry-agents", response_model=list[IndustryAgent], summary="Industry agents")
async def list_industry_agents(user_id: UUID = Depends(get_current_user_id)) -> list[dict]:
    counts: dict[str, int] = {}
    for issue in kb.list_issues():
        counts[issue["industry"]] = counts.get(issue["industry"], 0) + 1
    return [
        {**agent, "kbSourceCount": counts.get(agent["industry"], 0)}
        for agent in INDUSTRY_AGENTS
    ]
