"""
Bounce Board — KB ingestion jobs.

The frontend currently submits document *metadata* (name/size/type + industry
+ description), not file bytes, so ingestion creates a KB issue from that
metadata and embeds it. Jobs are tracked in-memory and stepped through
uploading → parsing → chunking → embedding → indexed so the polling UI shows
progress. (Real file parsing via src/llm/embedding_core extractors is the
future upgrade once the frontend uploads bytes.)
"""
import asyncio
import logging
import uuid
from typing import Optional

from fastapi import HTTPException

from src.bounce_board import kb

logger = logging.getLogger(__name__)

_STEPS = ["uploading", "parsing", "chunking", "embedding", "indexed"]

# job_id -> {jobId, fileName, step, progress}
_jobs: dict[str, dict] = {}


def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingest job not found")
    return job


def start_job(
    *,
    file_name: str,
    source_type: str,
    industry: str,
    description: Optional[str],
    org_id: Optional[str],
) -> str:
    job_id = f"bb-ingest-{uuid.uuid4().hex[:12]}"
    _jobs[job_id] = {"jobId": job_id, "fileName": file_name, "step": "uploading", "progress": 0}
    asyncio.create_task(
        _run_job(job_id, file_name, source_type, industry, description, org_id)
    )
    return job_id


async def _run_job(
    job_id: str,
    file_name: str,
    source_type: str,
    industry: str,
    description: Optional[str],
    org_id: Optional[str],
) -> None:
    job = _jobs[job_id]
    try:
        for i, step in enumerate(_STEPS[:-1]):
            job["step"] = step
            job["progress"] = round((i + 1) / len(_STEPS) * 100)
            await asyncio.sleep(0.6)

        title = file_name.rsplit(".", 1)[0]
        summary = description or f"Ingested document {file_name}."
        await kb.create_issue(
            title=title,
            industry=industry,
            source_type=source_type,
            severity="medium",
            summary=summary,
            content_md=f"## {file_name}\n\n{summary}",
            tags=["ingested"],
            org_id=org_id,
        )
        job["step"] = "indexed"
        job["progress"] = 100
    except Exception:  # noqa: BLE001 — surface failure via the job, not a crash
        logger.exception("Ingest job %s failed", job_id)
        # Frontend has no error step; leave the job at its last step so the
        # user sees it stalled rather than falsely indexed.
        job["progress"] = job.get("progress", 0)
