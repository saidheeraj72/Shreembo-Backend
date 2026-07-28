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

# The document is split into overlapping, paragraph-aware chunks; each chunk is
# mined concurrently, then duplicates are merged across chunks (map-reduce).
_CHARS_PER_CHUNK = 24_000
_CHUNK_OVERLAP = 1_500          # so an issue straddling a boundary survives in one chunk
_MINE_CONCURRENCY = 5           # concurrent chunk-mining LLM calls (rate-limit friendly)
_MAX_CHUNKS = 40                # safety backstop (~960k chars); truncation is LOGGED, never silent
_MAX_ISSUES_PER_DOC = 60        # backstop on the final distinct-issue count

_SEVERITIES = {"low", "medium", "high", "critical"}
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

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
        "charsExtracted": None,
        "chunks": None,
        "truncated": False,
        "error": None,
    }
    asyncio.create_task(
        _run_job(job_id, file_name, source_type, industry, description, org_id, file_bytes, file_type)
    )
    return job_id


def _extract_bytes(file_bytes: bytes, file_type: str) -> Optional[str]:
    """Extract text from raw bytes using the Documents module's extractor.

    One extractor per format family — PDFs go through pymupdf4llm (which OCRs
    its own empty pages), everything else through markitdown. The previous
    try-each-and-keep-the-longest loop existed to work around extractors that
    returned partial text; both of these either extract the document or return
    nothing, so the longest-wins comparison had nothing left to choose between.
    """
    from src.llm.embedding_core import _extract_pdf, _extract_with_markitdown
    from src.utils.text_utils import sanitize_text

    if file_type == "pdf":
        text = (_extract_pdf(file_bytes) or "").strip()
        extractor = "pymupdf4llm"
    else:
        text = (_extract_with_markitdown(file_bytes, file_type) or "").strip()
        extractor = "markitdown"

    logger.info("[BB-INGEST]   extractor %-12s -> %d chars", extractor, len(text))
    return sanitize_text(text) if text else None


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


def _chunk_text(text: str) -> tuple[list[str], bool]:
    """Split text into overlapping, paragraph-aware chunks covering the whole doc.

    Returns (chunks, truncated). ``truncated`` is True when the document was large
    enough to hit the _MAX_CHUNKS backstop, so the tail was dropped — the caller
    logs and surfaces this rather than dropping content silently.
    """
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        # A single paragraph larger than a whole chunk is hard-split (rare).
        while len(para) > _CHARS_PER_CHUNK:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:_CHARS_PER_CHUNK])
            para = para[_CHARS_PER_CHUNK - _CHUNK_OVERLAP :]
        # Close the current chunk if this paragraph would overflow it, seeding
        # the next chunk with the overlap tail so nothing falls through a seam.
        if current and len(current) + len(para) + 2 > _CHARS_PER_CHUNK:
            chunks.append(current)
            current = current[-_CHUNK_OVERLAP :]
        current = f"{current}\n\n{para}" if current else para
    if current.strip():
        chunks.append(current)

    truncated = len(chunks) > _MAX_CHUNKS
    return chunks[:_MAX_CHUNKS], truncated


def _merge_group(items: list[dict]) -> dict:
    """Consolidate duplicate issue dicts: richest content, unioned tags, max severity."""
    base = max(items, key=lambda c: len(c.get("contentMd") or c.get("summary") or ""))
    tags: list[str] = []
    for c in items:
        for t in c.get("tags") or []:
            if t not in tags:
                tags.append(t)
    severity = max(
        (c.get("severity") for c in items if c.get("severity") in _SEVERITIES),
        key=lambda s: _SEVERITY_RANK.get(s, 1),
        default="medium",
    )
    return {**base, "tags": tags, "severity": severity}


_MERGE_PROMPT = """You are given a numbered list of candidate business ISSUES mined
from different sections of the SAME document. Some describe the same underlying
issue in different words (a genuine duplicate); most are distinct.

Group the candidates so each group is exactly ONE distinct issue. A unique issue
is its own single-element group. Every index must appear in exactly one group.

Return STRICT JSON: {"groups": [[0, 3], [1], [2, 4], ...]}
Two candidates belong in the same group ONLY if they describe the same underlying
problem. When unsure, keep them separate."""


async def _merge_candidates(candidates: list[dict]) -> list[dict]:
    """Reduce step: group duplicate candidates via one LLM pass, merge each group.

    Falls back to normalized-title dedup if the merge call fails."""
    from src.bounce_board.pipeline import _client, _llm_json

    if len(candidates) <= 1:
        return candidates

    groups: list[list[int]] = []
    try:
        data = await _llm_json(
            _client(),
            _MERGE_PROMPT,
            {
                "candidates": [
                    {"index": i, "title": c.get("title"), "summary": c.get("summary")}
                    for i, c in enumerate(candidates)
                ]
            },
        )
        groups = [
            [i for i in g if isinstance(i, int) and 0 <= i < len(candidates)]
            for g in (data.get("groups") or [])
        ]
    except Exception:  # noqa: BLE001 — merge is best-effort; fall back below
        logger.warning("[BB-INGEST] merge pass failed — falling back to title dedup")

    # Fallback / repair: normalized-title dedup when the LLM gave no usable groups.
    if not any(groups):
        seen: dict[str, int] = {}
        for i, c in enumerate(candidates):
            key = (c.get("title") or "").strip().lower()
            if key and key not in seen:
                seen[key] = i
        groups = [[i] for i in seen.values()]

    # Any candidate the LLM forgot becomes its own singleton (never drop silently).
    grouped = {i for g in groups for i in g}
    groups.extend([i] for i in range(len(candidates)) if i not in grouped)

    return [_merge_group([candidates[i] for i in g]) for g in groups if g]


async def _mine_issues(
    text: str, file_name: str, description: Optional[str], job: Optional[dict] = None
) -> list[dict]:
    """Map-reduce over the whole document: mine each chunk concurrently, then
    consolidate duplicates across chunks. Returns distinct issue dicts."""
    from src.bounce_board.pipeline import _client, _llm_json

    chunks, truncated = _chunk_text(text)
    if job is not None:
        _update(job, chunks=len(chunks), truncated=truncated)
    if truncated:
        logger.warning(
            "[BB-INGEST] LARGE document: %d chars exceeds %d-chunk cap — tail dropped (truncated)",
            len(text), _MAX_CHUNKS,
        )
    if not chunks:
        return []

    client = _client()
    sem = asyncio.Semaphore(_MINE_CONCURRENCY)

    async def _mine_chunk(idx: int, chunk: str) -> list[dict]:
        async with sem:
            data = await _llm_json(
                client,
                _ISSUE_MINING_PROMPT,
                {"fileName": file_name, "userDescription": description or "", "documentText": chunk},
            )
            raws = [r for r in (data.get("issues") or []) if (r.get("title") or "").strip()]
            logger.info("[BB-INGEST]   chunk %d/%d -> %d issue(s)", idx + 1, len(chunks), len(raws))
            return raws

    results = await asyncio.gather(*[_mine_chunk(i, c) for i, c in enumerate(chunks)])
    candidates = [raw for chunk_issues in results for raw in chunk_issues]
    logger.info(
        "[BB-INGEST] mined %d raw candidate(s) across %d chunk(s)", len(candidates), len(chunks)
    )

    if len(chunks) > 1 and len(candidates) > 1:
        merged = await _merge_candidates(candidates)
        logger.info(
            "[BB-INGEST] merged %d candidate(s) -> %d distinct issue(s)", len(candidates), len(merged)
        )
    else:
        merged = candidates

    return merged[:_MAX_ISSUES_PER_DOC]


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

        # chunking — split + map-reduce mine the text for issues
        _update(job, step="chunking", progress=40, charsExtracted=len(text) if text else 0)

        mined: list[dict] = []
        if text:
            logger.info("[BB-INGEST] %s step=chunking — mining issues via LLM...", job_id)
            mined = await _mine_issues(text, file_name, description, job)
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
    except Exception as e:  # noqa: BLE001 — surface failure via the job, not a crash
        logger.exception("[BB-INGEST] %s FAILED", job_id)
        _update(
            job,
            step="error",
            error=f"Ingestion failed while {job.get('step', 'processing')}: {type(e).__name__}",
        )
