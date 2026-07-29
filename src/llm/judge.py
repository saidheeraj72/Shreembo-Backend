"""Answer judge — verifies a drafted RAG answer against its own sources.

Runs after the answer has streamed to the user but before it is persisted. It
does three things in one model call:

  1. checks every claim against the retrieved passages and flags the ones that
     are unsupported or contradicted,
  2. reports which sources the answer actually rests on, so the Sources panel
     stops listing passages that were retrieved but never used,
  3. returns a corrected answer with the unsupported parts removed or qualified.

It never adds information. Every failure path — timeout, malformed output, an
implausible revision — keeps the original answer and the full source list; a
judge that can delete a good answer is worse than no judge.
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.config import settings
from src.core.openai_client import openai_client
from src.llm.citations import remap_citations, strip_invalid_citations

logger = logging.getLogger(__name__)


_INSTRUCTIONS = """\
You are a strict factuality checker for a document question-answering system.

You are given a user's question, the numbered source passages that were \
retrieved for it, and a draft answer written from those passages.

Check the draft answer sentence by sentence:
- A claim is "supported" only if a source passage states it. Paraphrase is fine; \
inference beyond what the passage says is not.
- A claim is "contradicted" if a source states something different — a different \
number, date, name, or conclusion.
- A claim is "unsupported" if no passage covers it, including anything that looks \
like general world knowledge rather than something from these documents.
- Statements that are not factual claims (greetings, "here is what I found", \
structural transitions) need no source. Ignore them.
- Arithmetic shown in the answer counts as supported when its inputs are supported.

Then produce a revised answer. Rules for the revision, in order of importance:
1. NEVER add a fact, figure, name, date, or qualifier that is not in the sources. \
You may only delete, correct-to-match-the-source, or hedge.
2. Remove contradicted claims and correct them to what the source actually says.
3. Remove unsupported claims. If removing one leaves a gap the user asked about, \
say plainly that the documents do not cover it.
4. Otherwise keep the draft as it is — including its wording, structure, markdown, \
tables and formatting. If everything checks out, return the draft unchanged.
5. Keep the inline [n] citation markers, using the SAME source numbers you were \
given. Do not renumber them. Do not add a bibliography.

Finally, list in used_source_ids every source number the revised answer actually \
relies on — not everything you were shown. Sources that support nothing in the \
final answer must be left out.

Set verdict to:
- "supported" if every claim checked out,
- "partially_supported" if you removed or corrected something but a real answer remains,
- "unsupported" if essentially nothing in the draft is backed by the sources."""


_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "issues", "used_source_ids", "revised_answer"],
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
                        "description": "The problematic sentence, quoted from the draft.",
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
        "used_source_ids": {
            "type": "array",
            "description": "Source numbers the revised answer actually relies on.",
            "items": {"type": "integer"},
        },
        "revised_answer": {
            "type": "string",
            "description": "The corrected answer, keeping the original [n] numbering.",
        },
    },
}


_NO_ANSWER = (
    "I could not find support for this in your documents. The passages I "
    "retrieved do not cover what you asked."
)


def _render_sources(sources: List[dict]) -> str:
    """Render the numbered sources the way the judge should see them."""
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
            body = s.get("chunk_text", "")
        blocks.append(f"{head}\n{body}")
    return "\n\n".join(blocks)


def _should_skip(answer: str, sources: List[dict]) -> Optional[str]:
    """Return a reason to skip the judge, or None to run it."""
    if not settings.RAG_JUDGE_ENABLED:
        return "disabled"
    if not sources:
        # Nothing was retrieved, so there is nothing to check the answer against
        return "no sources"
    if not answer or not answer.strip():
        return "empty answer"
    if len(answer) < settings.RAG_JUDGE_MIN_ANSWER_CHARS:
        return "answer too short"
    return None


def _prune_and_renumber(
    sources: List[dict], used_ids: List[int], answer: str
) -> Tuple[List[dict], str]:
    """Keep only the used sources, renumber them 1..N, and fix the markers.

    Order is preserved, so the strongest passage stays Source 1.
    """
    used = {int(i) for i in used_ids if isinstance(i, (int, float))}
    kept = [s for s in sources if s.get("citation") in used]
    if not kept:
        return sources, answer

    mapping: Dict[int, int] = {}
    pruned: List[dict] = []
    for new_number, source in enumerate(kept, 1):
        mapping[source["citation"]] = new_number
        pruned.append({**source, "citation": new_number})

    return pruned, remap_citations(answer, mapping)


async def judge_answer(
    question: str,
    answer: str,
    sources: List[dict],
) -> Dict[str, Any]:
    """Verify *answer* against *sources*.

    Returns a dict with ``answer``, ``sources``, ``verdict``, ``issues`` and
    ``judged``. On any failure ``judged`` is False and the inputs come back
    untouched.
    """
    unchanged = {
        "answer": answer,
        "sources": sources,
        "verdict": None,
        "issues": [],
        "judged": False,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }

    skip = _should_skip(answer, sources)
    if skip:
        logger.debug("Judge skipped: %s", skip)
        return unchanged

    payload = (
        f"# Question\n{question}\n\n"
        f"# Sources\n{_render_sources(sources)}\n\n"
        f"# Draft answer\n{answer}"
    )

    try:
        response = await asyncio.wait_for(
            openai_client.client.responses.create(
                model=settings.RAG_JUDGE_MODEL or settings.OPENAI_CHAT_MODEL,
                instructions=_INSTRUCTIONS,
                input=[{"role": "user", "content": payload}],
                reasoning={"effort": "low"},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "answer_verification",
                        "strict": True,
                        "schema": _SCHEMA,
                    }
                },
            ),
            timeout=settings.RAG_JUDGE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("Judge timed out after %ss — keeping draft", settings.RAG_JUDGE_TIMEOUT)
        return unchanged
    except Exception as e:
        logger.error("Judge call failed (%s) — keeping draft", e)
        return unchanged

    usage = getattr(response, "usage", None)
    unchanged["prompt_tokens"] = getattr(usage, "input_tokens", 0) or 0
    unchanged["completion_tokens"] = getattr(usage, "output_tokens", 0) or 0

    try:
        verdict_data = json.loads(response.output_text)
        if not isinstance(verdict_data, dict):
            raise ValueError(f"expected an object, got {type(verdict_data).__name__}")
        verdict = verdict_data.get("verdict")
        issues = verdict_data.get("issues") or []
        revised = (verdict_data.get("revised_answer") or "").strip()
        used_ids = verdict_data.get("used_source_ids") or []
        if not isinstance(used_ids, list):
            used_ids = []
    except (AttributeError, TypeError, ValueError) as e:
        # The schema is strict, so this should not happen — but a judge that
        # raises would kill an answer the user has already read.
        logger.error("Judge returned unusable output (%s) — keeping draft", e)
        return unchanged

    # ── Nothing in the answer is supported ────────────────────────────────
    if verdict == "unsupported":
        logger.info("Judge: answer unsupported by %d sources — replacing", len(sources))
        return {
            **unchanged,
            "answer": revised or _NO_ANSWER,
            "sources": [],
            "verdict": verdict,
            "issues": issues,
            "judged": True,
        }

    # ── Sanity-check the revision before trusting it ──────────────────────
    # A revision may only shrink or stay roughly the same size. Growth means the
    # judge wrote new prose instead of pruning, which is exactly what it was
    # told not to do. A rejected revision discards the whole verdict, source
    # pruning included: a judge we do not trust to rewrite the answer is not one
    # to trust about which sources it rests on.
    if not revised:
        logger.warning("Judge returned an empty revision — keeping draft")
        return unchanged
    if len(revised) > len(answer) * settings.RAG_JUDGE_MAX_GROWTH:
        logger.warning(
            "Judge revision grew %.1fx (%d → %d chars) — keeping draft",
            len(revised) / max(len(answer), 1), len(answer), len(revised),
        )
        return unchanged

    try:
        # Markers must still resolve against the pre-prune numbering
        revised = strip_invalid_citations(revised, len(sources))
        pruned_sources, final_answer = _prune_and_renumber(sources, used_ids, revised)
    except Exception as e:
        logger.error("Judge post-processing failed (%s) — keeping draft", e)
        return unchanged

    if len(pruned_sources) < len(sources):
        logger.info(
            "Judge: %d/%d sources actually used, %d issue(s), verdict=%s",
            len(pruned_sources), len(sources), len(issues), verdict,
        )

    return {
        **unchanged,
        "answer": final_answer,
        "sources": pruned_sources,
        "verdict": verdict,
        "issues": issues,
        "judged": True,
    }
