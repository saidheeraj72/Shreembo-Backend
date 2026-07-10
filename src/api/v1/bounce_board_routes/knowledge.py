"""
Bounce Board — knowledge base endpoints.

  GET  /kb/issues            browse/filter KB issues
  GET  /kb/issues/{id}       one KB issue
  GET  /kb/mind-map          industry → category → issue graph
  POST /kb/ingest            start a metadata-based ingest job
  GET  /kb/ingest/{job_id}   poll an ingest job
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from src.bounce_board import ingest as ingest_jobs
from src.bounce_board import kb
from src.bounce_board.constants import INDUSTRIES, SOURCE_TYPE_LABELS
from src.core.dependencies import get_current_user_id
from src.models.bounce_board import (
    IngestJob,
    KBIssue,
    KBMindMap,
)

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/kb/issues", response_model=list[KBIssue], summary="Browse the knowledge base")
async def list_kb_issues(
    industry: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None, alias="sourceType"),
    search: Optional[str] = Query(None),
    user_id: UUID = Depends(get_current_user_id),
) -> list[dict]:
    return kb.list_issues(industry=industry, source_type=source_type, search=search)


@router.get("/kb/issues/{issue_id}", response_model=KBIssue, summary="Get one KB entry")
async def get_kb_issue(
    issue_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    return kb.get_issue(issue_id)


@router.get("/kb/mind-map", response_model=KBMindMap, summary="Knowledge base mind map")
async def get_kb_mind_map(
    industry: Optional[str] = Query(None),
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    return kb.mind_map(industry=industry)


@router.post("/kb/ingest", summary="Ingest a document into the KB (extracts issues from its content)")
async def ingest_document(
    file: UploadFile = File(...),
    source_type: str = Form(..., alias="sourceType"),
    industry: str = Form(...),
    description: Optional[str] = Form(None),
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    if source_type not in SOURCE_TYPE_LABELS:
        raise HTTPException(status_code=422, detail=f"Invalid sourceType '{source_type}'")
    if industry not in INDUSTRIES:
        raise HTTPException(status_code=422, detail=f"Invalid industry '{industry}'")

    file_bytes = await file.read()
    if len(file_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 25 MB upload limit")

    file_name = file.filename or "document"
    file_type = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "txt"

    job_id = ingest_jobs.start_job(
        file_name=file_name,
        source_type=source_type,
        industry=industry,
        description=description,
        org_id=None,
        file_bytes=file_bytes,
        file_type=file_type,
    )
    return {"jobId": job_id}


@router.get("/kb/ingest/{job_id}", response_model=IngestJob, summary="Poll an ingest job")
async def get_ingest_job(
    job_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    return ingest_jobs.get_job(job_id)
