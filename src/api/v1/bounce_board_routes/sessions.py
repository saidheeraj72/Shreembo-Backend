"""
Bounce Board — analysis session endpoints.

  GET    /stats                          headline numbers for the dashboard
  POST   /detect-context                 wizard preview: detect context from the draft
  GET    /sessions                       list the user's analyses
  POST   /sessions                       create a draft analysis
  GET    /sessions/{id}                  full session (pushed over WS while running)
  POST   /sessions/{id}/attachments      upload a supporting document (multipart)
  POST   /sessions/{id}/start            (re)run the pipeline; ?from_stage= resumes
  POST   /sessions/{id}/ask              ask the board a follow-up question
  DELETE /sessions/{id}                  delete an analysis
  GET    /sessions/{id}/discussion       board discussion
  GET    /sessions/{id}/recommendations  ranked recommendations
  GET    /sessions/{id}/report           final report
  GET    /industry-agents                the four industry agent templates
"""
import asyncio
import logging
import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from src.bounce_board import copilot, export, kb, pipeline, store
from src.bounce_board.constants import INDUSTRY_AGENTS
from src.core.dependencies import get_current_user
from src.models.bounce_board import (
    AskBoardRequest,
    BounceBoardStats,
    ChatMessage,
    ChatRequest,
    ContextDetection,
    ContextDetectRequest,
    DiscussionMessage,
    DiscussionState,
    IndustryAgent,
    Recommendation,
    Report,
    Session,
    SessionAttachment,
    SessionInput,
    SessionSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_ATTACHMENTS = 5
_MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15 MB
_MAX_ATTACHMENT_TEXT_CHARS = 30_000


def _user_id(user: dict) -> UUID:
    return UUID(str(user["id"]))


def _org_id(user: dict) -> Optional[str]:
    return user.get("org_id")


@router.get("/stats", response_model=BounceBoardStats, summary="Dashboard stats")
async def get_stats(user: dict = Depends(get_current_user)) -> dict:
    return {
        **store.stats(_user_id(user)),
        "kbDocuments": kb.count_issues(org_id=_org_id(user)),
    }


@router.get("/sessions", response_model=list[SessionSummary], summary="List analyses")
async def list_sessions(user: dict = Depends(get_current_user)) -> list[dict]:
    return [store.session_to_summary(row) for row in store.list_sessions(_user_id(user))]


@router.post(
    "/detect-context",
    response_model=ContextDetection,
    summary="Preview context detection for the wizard (before a session exists)",
)
async def detect_context(
    req: ContextDetectRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        return await pipeline.detect_context_preview(req.model_dump(by_alias=True))
    except Exception as e:  # noqa: BLE001 — surfaced as a soft failure in the wizard
        logger.warning("Context detection preview failed: %s", e)
        raise HTTPException(status_code=502, detail="Context detection failed") from e


@router.post("/sessions", response_model=Session, summary="Create a draft analysis")
async def create_session(
    input: SessionInput,
    user: dict = Depends(get_current_user),
) -> dict:
    row = store.create_session(_user_id(user), _org_id(user), input.model_dump(by_alias=True))
    return store.session_to_api(row)


@router.get("/sessions/{session_id}", response_model=Session, summary="Get a full session")
async def get_session(
    session_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    return store.session_to_api(store.get_session(_user_id(user), session_id))


@router.post(
    "/sessions/{session_id}/attachments",
    response_model=list[SessionAttachment],
    summary="Upload a supporting document (its text feeds the analysis)",
)
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
) -> list[dict]:
    row = store.get_session(_user_id(user), session_id)
    if row["status"] == "running":
        raise HTTPException(status_code=409, detail="Cannot attach files while the analysis is running")
    attachments = row.get("attachments") or []
    if len(attachments) >= _MAX_ATTACHMENTS:
        raise HTTPException(status_code=422, detail=f"At most {_MAX_ATTACHMENTS} attachments per analysis")

    file_bytes = await file.read()
    if len(file_bytes) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 15 MB upload limit")

    file_name = file.filename or "document"
    file_type = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "txt"

    from src.bounce_board.ingest import _extract_bytes  # extractor stack reuse

    text: Optional[str] = None
    try:
        text = await asyncio.to_thread(_extract_bytes, file_bytes, file_type)
    except Exception:  # noqa: BLE001 — an unparseable file still gets attached (0 chars)
        logger.exception("Attachment text extraction failed for %s", file_name)

    attachment = {
        "id": f"att-{uuid.uuid4().hex[:10]}",
        "name": file_name,
        "sizeBytes": len(file_bytes),
        "mimeType": file.content_type or "application/octet-stream",
        "charsExtracted": len(text) if text else 0,
        "text": (text or "")[:_MAX_ATTACHMENT_TEXT_CHARS],
    }
    attachments = attachments + [attachment]
    store.update_session(session_id, {"attachments": attachments})
    return [{k: v for k, v in a.items() if k != "text"} for a in attachments]


@router.post("/sessions/{session_id}/start", summary="(Re)run the analysis pipeline")
async def start_session(
    session_id: str,
    from_stage: Optional[str] = Query(None, alias="fromStage"),
    user: dict = Depends(get_current_user),
) -> dict:
    # Return JSON (not 204): the frontend fetch wrapper always parses a body.
    row = store.get_session(_user_id(user), session_id)
    if row["status"] == "running":
        return {"status": "already_running"}

    patch, effective_from = store.reset_patch(row, from_stage)
    store.update_session(session_id, patch)
    pipeline.start(session_id)
    return {"status": "started", "fromStage": effective_from}


@router.post(
    "/sessions/{session_id}/ask",
    response_model=DiscussionMessage,
    summary="Ask the board a follow-up question",
)
async def ask_board(
    session_id: str,
    request: AskBoardRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    row = store.get_session(_user_id(user), session_id)
    question = (request.question or "").strip()
    if len(question) < 3:
        raise HTTPException(status_code=422, detail="Ask a real question")
    if row["status"] == "running":
        raise HTTPException(status_code=409, detail="The analysis is still running")
    if not (row.get("discussion") or {}).get("messages"):
        raise HTTPException(status_code=409, detail="Run the analysis before asking the board")
    return await pipeline.ask_board(row, question, request.persona_id)


@router.get(
    "/sessions/{session_id}/chat",
    response_model=list[ChatMessage],
    summary="Copilot chat history",
)
async def get_chat(
    session_id: str,
    user: dict = Depends(get_current_user),
) -> list[dict]:
    row = store.get_session(_user_id(user), session_id)
    return copilot.get_history(row)


@router.post(
    "/sessions/{session_id}/chat",
    response_model=list[ChatMessage],
    summary="Talk to the session copilot (can modify the analysis)",
)
async def post_chat(
    session_id: str,
    request: ChatRequest,
    user: dict = Depends(get_current_user),
) -> list[dict]:
    row = store.get_session(_user_id(user), session_id)
    message = (request.message or "").strip()
    if len(message) < 2:
        raise HTTPException(status_code=422, detail="Say something first")
    return await copilot.chat(row, message)


_EXPORTERS = {
    "md": (export.to_markdown, "text/markdown; charset=utf-8"),
    "pdf": (export.to_pdf, "application/pdf"),
    "docx": (
        export.to_docx,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}


@router.get(
    "/sessions/{session_id}/report/export",
    summary="Export the report (md / pdf / docx)",
)
async def export_report(
    session_id: str,
    format: str = Query("pdf"),
    user: dict = Depends(get_current_user),
) -> Response:
    if format not in _EXPORTERS:
        raise HTTPException(status_code=422, detail=f"Unsupported format '{format}'")
    row = store.get_session(_user_id(user), session_id)
    if not row.get("report"):
        raise HTTPException(status_code=404, detail="Report not generated yet")
    build, media_type = _EXPORTERS[format]
    content = await asyncio.to_thread(build, row)
    if isinstance(content, str):
        content = content.encode("utf-8")
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename(row, format)}"'
        },
    )


@router.delete("/sessions/{session_id}", summary="Delete an analysis")
async def delete_session(
    session_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    store.delete_session(_user_id(user), session_id)
    return {"status": "deleted"}


@router.get(
    "/sessions/{session_id}/discussion",
    response_model=DiscussionState,
    summary="Board discussion",
)
async def get_discussion(
    session_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    row = store.get_session(_user_id(user), session_id)
    return row.get("discussion") or {"status": "idle", "activeAgentId": None, "messages": []}


@router.get(
    "/sessions/{session_id}/recommendations",
    response_model=list[Recommendation],
    summary="Ranked recommendations",
)
async def get_recommendations(
    session_id: str,
    user: dict = Depends(get_current_user),
) -> list[dict]:
    row = store.get_session(_user_id(user), session_id)
    return row.get("recommendations") or []


@router.get("/sessions/{session_id}/report", response_model=Report, summary="Final report")
async def get_report(
    session_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    row = store.get_session(_user_id(user), session_id)
    report = row.get("report")
    if not report:
        raise HTTPException(status_code=404, detail="Report not generated yet")
    return report


@router.get("/industry-agents", response_model=list[IndustryAgent], summary="Industry agents")
async def list_industry_agents(user: dict = Depends(get_current_user)) -> list[dict]:
    counts: dict[str, int] = {}
    for issue in kb.list_issues(org_id=_org_id(user)):
        counts[issue["industry"]] = counts.get(issue["industry"], 0) + 1
    return [
        {**agent, "kbSourceCount": counts.get(agent["industry"], 0)}
        for agent in INDUSTRY_AGENTS
    ]
