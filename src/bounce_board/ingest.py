"""
Bounce Board — KB ingestion jobs.

Real document ingestion: the route hands us the uploaded file's bytes, we
extract text (same extractor stack as the Documents module), an LLM mines the
distinct business issues out of it, and each issue becomes its own KB entry
embedded into Qdrant. Jobs are tracked in-memory and stepped through
uploading → parsing → chunking → embedding → indexed; every step change is
published to the event broker so the job's WebSocket pushes it to the UI.

If no bytes are provided (or nothing can be extracted), we fall back to a
single metadata-based entry so the document still becomes searchable.
"""
import asyncio
import logging
import uuid
from typing import Optional

from fastapi import HTTPException

from src.bounce_board import events, kb

logger = logging.getLogger(__name__)

# How much document text the issue-mining LLM sees per call, and how many
# calls we make for long documents.
_CHARS_PER_BATCH = 24_000
_MAX_BATCHES = 4
_MAX_ISSUES_PER_DOC = 12

_SEVERITIES = {"low", "medium", "high", "critical"}

# job_id -> {jobId, fileName, step, progress, issuesCreated}
_jobs: dict[str, dict] = {}


def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingest job not found")
    return job


def peek_job(job_id: str) -> Optional[dict]:
    """Non-raising lookup for the WebSocket endpoint."""
    return _jobs.get(job_id)


def _update(job: dict, **fields) -> None:
    """Mutate a job and push the new state to its WebSocket subscribers."""
    job.update(fields)
    events.publish(job["jobId"], {"type": "ingestJob", "job": dict(job)})


def start_job(
    *,
    file_name: str,
    source_type: str,
    industry: str,
    description: Optional[str],
    org_id: Optional[str],
    file_bytes: Optional[bytes] = None,
    file_type: Optional[str] = None,
) -> str:
    job_id = f"bb-ingest-{uuid.uuid4().hex[:12]}"
    _jobs[job_id] = {
        "jobId": job_id,
        "fileName": file_name,
        "step": "uploading",
        "progress": 5,
        "issuesCreated": None,
    }
    asyncio.create_task(
        _run_job(job_id, file_name, source_type, industry, description, org_id, file_bytes, file_type)
    )
    return job_id


# An extraction shorter than this is treated as suspect (e.g. pymupdf4llm
# keeping only a PDF's headers/footers) and the next extractor is tried too;
# the longest result wins.
_MIN_SUBSTANTIAL_CHARS = 1_000


def _extract_bytes(file_bytes: bytes, file_type: str) -> Optional[str]:
    """Extract text from raw bytes using the Documents module's extractor stack."""
    from src.llm.embedding_core import (
        _extract_pdf_with_ocr,
        _extract_pdf_with_pymupdf,
        _extract_with_markitdown,
        _extract_with_unstructured,
    )
    from src.utils.text_utils import sanitize_text

    if file_type == "pdf":
        extractors = [
            ("unstructured", lambda: _extract_with_unstructured(file_bytes, file_type)),
            ("pymupdf4llm", lambda: _extract_pdf_with_pymupdf(file_bytes)),
            ("pymupdf+ocr", lambda: _extract_pdf_with_ocr(file_bytes)),
        ]
    else:
        extractors = [
            ("markitdown", lambda: _extract_with_markitdown(file_bytes, file_type)),
            ("unstructured", lambda: _extract_with_unstructured(file_bytes, file_type)),
        ]

    best = ""
    for name, extract in extractors:
        text = (extract() or "").strip()
        logger.info("[BB-INGEST]   extractor %-12s -> %d chars", name, len(text))
        if len(text) > len(best):
            best = text
        if len(best) >= _MIN_SUBSTANTIAL_CHARS:
            break
    return sanitize_text(best) if best else None


_ISSUE_MINING_PROMPT = """You analyze a business document and extract the distinct ISSUES it contains.

An "issue" is a specific problem, risk, incident, compliance gap, recurring
failure, unmet need or documented lesson worth remembering. Skip boilerplate,
headers, contact info and generic statements.

Return STRICT JSON:
{
  "issues": [
    {
      "title": "short specific title, max ~12 words",
      "summary": "2-3 sentence summary with the concrete facts/figures from the document",
      "severity": "low" | "medium" | "high" | "critical",
      "tags": ["2-4 short lowercase tags"],
      "contentMd": "markdown: the issue explained in 1-3 short paragraphs, preserving key numbers, plus a 'Recommended actions' bullet list if the document implies any"
    }
  ]
}
Rules:
- Each issue must be genuinely distinct — merge duplicates.
- Ground everything in the document text; do not invent facts.
- If the text contains no real issues, return {"issues": []}."""


async def _mine_issues(text: str, file_name: str, description: Optional[str]) -> list[dict]:
    """LLM pass(es) over the document text; returns raw issue dicts."""
    from src.bounce_board.pipeline import _client, _llm_json

    client = _client()
    batches = [
        text[i : i + _CHARS_PER_BATCH]
        for i in range(0, min(len(text), _CHARS_PER_BATCH * _MAX_BATCHES), _CHARS_PER_BATCH)
    ]
    issues: list[dict] = []
    seen_titles: set[str] = set()
    for batch in batches:
        data = await _llm_json(
            client,
            _ISSUE_MINING_PROMPT,
            {
                "fileName": file_name,
                "userDescription": description or "",
                "documentText": batch,
                "alreadyExtractedTitles": sorted(seen_titles),
            },
        )
        for raw in data.get("issues") or []:
            title = (raw.get("title") or "").strip()
            if not title or title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            issues.append(raw)
            if len(issues) >= _MAX_ISSUES_PER_DOC:
                return issues
    return issues


async def _run_job(
    job_id: str,
    file_name: str,
    source_type: str,
    industry: str,
    description: Optional[str],
    org_id: Optional[str],
    file_bytes: Optional[bytes],
    file_type: Optional[str],
) -> None:
    job = _jobs[job_id]
    logger.info(
        "[BB-INGEST] %s START file=%s type=%s bytes=%s industry=%s source=%s",
        job_id, file_name, file_type, len(file_bytes) if file_bytes else 0, industry, source_type,
    )
    try:
        # parsing — extract text off the event loop (CPU/OCR-bound)
        _update(job, step="parsing", progress=20)
        logger.info("[BB-INGEST] %s step=parsing — extracting text...", job_id)
        text: Optional[str] = None
        if file_bytes:
            try:
                text = await asyncio.to_thread(_extract_bytes, file_bytes, file_type or "txt")
            except Exception:  # noqa: BLE001 — extraction failure falls back to metadata
                logger.exception("[BB-INGEST] %s text extraction FAILED for %s", job_id, file_name)
        else:
            logger.warning("[BB-INGEST] %s NO file bytes received — will use metadata fallback", job_id)
        logger.info(
            "[BB-INGEST] %s extraction done -> %s chars",
            job_id, len(text) if text else 0,
        )

        # chunking — batch the text for the issue-mining LLM
        _update(job, step="chunking", progress=40)

        mined: list[dict] = []
        if text:
            logger.info("[BB-INGEST] %s step=chunking — mining issues via LLM...", job_id)
            mined = await _mine_issues(text, file_name, description)
            logger.info(
                "[BB-INGEST] %s mined %d issue(s) from %s (%d chars): %s",
                job_id, len(mined), file_name, len(text),
                [ (m.get("title") or "?")[:60] for m in mined ] or "NONE",
            )
        else:
            logger.warning("[BB-INGEST] %s no text extracted — skipping LLM mining", job_id)

        # embedding — create + embed one KB entry per mined issue
        _update(job, step="embedding", progress=60)

        created = 0
        base_title = file_name.rsplit(".", 1)[0]
        if mined:
            logger.info("[BB-INGEST] %s step=embedding — creating %d KB entries...", job_id, len(mined))
            for i, raw in enumerate(mined):
                severity = raw.get("severity") if raw.get("severity") in _SEVERITIES else "medium"
                await kb.create_issue(
                    title=raw.get("title") or f"{base_title} — issue {i + 1}",
                    industry=industry,
                    source_type=source_type,
                    severity=severity,
                    summary=raw.get("summary") or "",
                    content_md=(raw.get("contentMd") or raw.get("summary") or "")
                    + f"\n\n---\n*Source document: {file_name}*",
                    tags=[str(t) for t in (raw.get("tags") or [])][:4] + ["ingested"],
                    org_id=org_id,
                )
                created += 1
                _update(job, progress=60 + round(35 * (i + 1) / len(mined)))
        else:
            # Fallback: no bytes or no extractable issues — index the document
            # itself as a single entry so it is still searchable.
            logger.warning(
                "[BB-INGEST] %s FALLBACK — no issues mined, creating single metadata entry for %s",
                job_id, file_name,
            )
            summary = description or f"Ingested document {file_name}."
            await kb.create_issue(
                title=base_title,
                industry=industry,
                source_type=source_type,
                severity="medium",
                summary=summary,
                content_md=f"## {file_name}\n\n{summary}",
                tags=["ingested"],
                org_id=org_id,
            )
            created = 1

        _update(job, issuesCreated=created, step="indexed", progress=100)
        logger.info("[BB-INGEST] %s DONE step=indexed issuesCreated=%d", job_id, created)
    except Exception:  # noqa: BLE001 — surface failure via the job, not a crash
        logger.exception("[BB-INGEST] %s FAILED", job_id)
        # Frontend has no error step; leave the job at its last step so the
        # user sees it stalled rather than falsely indexed.
        _update(job, progress=job.get("progress", 0))
