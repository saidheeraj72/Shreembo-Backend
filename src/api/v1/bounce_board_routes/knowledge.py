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

from fastapi import APIRouter, Depends, Query

from src.bounce_board import ingest as ingest_jobs
from src.bounce_board import kb
from src.core.dependencies import get_current_user_id
from src.models.bounce_board import (
    IngestJob,
    IngestRequest,
    KBIssue,
    KBMindMap,
)

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


@router.post("/kb/ingest", summary="Ingest a document (metadata-based) into the KB")
async def ingest_document(
    request: IngestRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    job_id = ingest_jobs.start_job(
        file_name=request.file_name,
        source_type=request.source_type,
        industry=request.industry,
        description=request.description,
        org_id=None,
    )
    return {"jobId": job_id}


@router.get("/kb/ingest/{job_id}", response_model=IngestJob, summary="Poll an ingest job")
async def get_ingest_job(
    job_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    return ingest_jobs.get_job(job_id)
