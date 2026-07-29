"""Hallucination check — reports claims a RAG answer's own sources do not support.

Runs after the answer has streamed to the user, on every answer that had
retrieved sources, regardless of length.

Its only job is detection. It does not rewrite the answer, does not prune the
source list, and does not decide what the user sees — a checker that edits the
thing it is checking cannot be trusted to report on it, and a bad revision
destroys a good answer. Grounding quality belongs to the generation step (see
``RAG_REASONING_EFFORT``); this is the smoke alarm, not the sprinkler.

Every failure path — timeout, malformed output, a refusal — returns no verdict
and leaves the answer exactly as it was.
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from src.config import settings
from src.core.openai_client import openai_client

logger = logging.getLogger(__name__)


_INSTRUCTIONS = """\
You are a strict factuality checker for a document question-answering system.

You are given a user's question, the numbered source passages that were \
retrieved for it, and the answer that was written from those passages.

Check the answer claim by claim and report only what fails:
- A claim is "supported" only if a source passage states it. Paraphrase is fine; \
inference beyond what the passage says is not. Supported claims are not reported.
- A claim is "contradicted" if a source states something different — a different \
number, date, name, or conclusion.
- A claim is "unsupported" if no passage covers it, including anything that looks \
like general world knowledge rather than something from these documents.
- Statements that are not factual claims (greetings, "here is what I found", \
structural transitions, questions back to the user) need no source. Ignore them.
- Arithmetic shown in the answer counts as supported when its inputs are supported.
- An answer that correctly says the documents do not cover something is supported, \
not unsupported. Reporting the absence of information is not a hallucination.

Quote each problematic claim verbatim from the answer so it can be located, and \
say what the sources state instead — or that they say nothing about it.

Do NOT rewrite, correct, or improve the answer. Do not suggest replacement text. \
Report only.

Set verdict to:
- "supported" if every claim checked out,
- "partially_supported" if some claims failed but a real, grounded answer remains,
- "unsupported" if essentially nothing in the answer is backed by the sources."""


_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "issues"],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["supported", "partially_supported", "unsupported"],
        },
        "issues": {
            "type": "array",
            "description": "Claims that failed the check. Empty when nothing is wrong.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim", "status", "explanation"],
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": "The problematic sentence, quoted from the answer.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["unsupported", "contradicted"],
                    },
                    "explanation": {
                        "type": "string",
                        "description": "What the sources say instead, or that they say nothing.",
                    },
                },
            },
        },
    },
}


def _render_sources(sources: List[dict]) -> str:
    """Render the numbered sources the way the checker should see them.

    ``full_text`` is preferred over ``chunk_text`` when present: sources that
    came from a full-document read carry only a short preview in
    ``chunk_text``, and checking an answer against a preview of the document it
    was written from reports the rest of the answer as unsupported.
    """
    blocks: List[str] = []
    for s in sources:
        number = s.get("citation")
        if s.get("kind") == "web":
            head = f"Source {number} (web): {s.get('title', '')} — {s.get('url', '')}"
            body = s.get("snippet", "")
        else:
            head = f"Source {number}: {s.get('document_name', 'Unknown')}"
            if s.get("section_header"):
                head += f" — {s['section_header']}"
            if s.get("page_numbers"):
                head += f" (p. {', '.join(str(p) for p in s['page_numbers'])})"
            body = s.get("full_text") or s.get("chunk_text", "")
        blocks.append(f"{head}\n{body}")
    return "\n\n".join(blocks)


def _should_skip(answer: str, sources: List[dict]) -> Optional[str]:
    """Return a reason to skip the check, or None to run it.

    Deliberately short: if RAG produced sources, the answer gets checked. There
    is no answer-length floor — a one-line answer inventing a figure is exactly
    the case worth catching.
    """
    if not settings.RAG_JUDGE_ENABLED:
        return "disabled"
    if not sources:
        # Nothing was retrieved, so there is nothing to check the answer against
        return "no sources"
    if not answer or not answer.strip():
        return "empty answer"
    return None


async def judge_answer(
    question: str,
    answer: str,
    sources: List[dict],
) -> Dict[str, Any]:
    """Check *answer* against *sources* and report unsupported claims.

    Returns ``verdict``, ``issues``, ``judged`` and token counts. The answer and
    sources are never modified — callers keep their own copies. On any failure
    ``judged`` is False and ``verdict`` is None.
    """
    result: Dict[str, Any] = {
        "verdict": None,
        "issues": [],
        "judged": False,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }

    skip = _should_skip(answer, sources)
    if skip:
        logger.debug("Hallucination check skipped: %s", skip)
        return result

    payload = (
        f"# Question\n{question}\n\n"
        f"# Sources\n{_render_sources(sources)}\n\n"
        f"# Answer to check\n{answer}"
    )

    try:
        response = await asyncio.wait_for(
            openai_client.client.responses.create(
                model=settings.RAG_JUDGE_MODEL or settings.OPENAI_CHAT_MODEL,
                instructions=_INSTRUCTIONS,
                input=[{"role": "user", "content": payload}],
                reasoning={"effort": settings.RAG_JUDGE_REASONING_EFFORT},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "hallucination_check",
                        "strict": True,
                        "schema": _SCHEMA,
                    }
                },
            ),
            timeout=settings.RAG_JUDGE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Hallucination check timed out after %ss — no verdict",
            settings.RAG_JUDGE_TIMEOUT,
        )
        return result
    except Exception as e:
        logger.error("Hallucination check failed (%s) — no verdict", e)
        return result

    usage = getattr(response, "usage", None)
    result["prompt_tokens"] = getattr(usage, "input_tokens", 0) or 0
    result["completion_tokens"] = getattr(usage, "output_tokens", 0) or 0

    try:
        data = json.loads(response.output_text)
        if not isinstance(data, dict):
            raise ValueError(f"expected an object, got {type(data).__name__}")
        verdict = data.get("verdict")
        issues = data.get("issues") or []
        if not isinstance(issues, list):
            issues = []
    except (AttributeError, TypeError, ValueError) as e:
        # The schema is strict, so this should not happen.
        logger.error("Hallucination check returned unusable output (%s)", e)
        return result

    if verdict != "supported" or issues:
        logger.warning(
            "Hallucination check: verdict=%s, %d unsupported claim(s) over %d sources",
            verdict, len(issues), len(sources),
        )

    result.update({"verdict": verdict, "issues": issues, "judged": True})
    return result
