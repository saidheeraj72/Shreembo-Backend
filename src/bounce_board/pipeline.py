"""
Bounce Board — the 10-stage analysis pipeline.

Launched fire-and-forget (asyncio.create_task) from the start endpoint; after
every stage it writes the artifact onto the session row and the store publishes
it to the session WebSocket. During board_discussion each persona message is
appended to the row as it is generated, so the UI streams the debate live.

v2 design:
  - intake actually parses the uploaded attachments (extracted text lives on
    the row) and KPI inputs; its digest feeds every later stage.
  - agent_routing composes a problem-specific board: a domain expert plus 4-6
    personas with tailored focus areas, round topics and a framework selection
    — persisted as the session's `board` so the UI renders the real cast.
  - framework analysis runs only the selected frameworks; results are written
    incrementally as each completes (live UI + heartbeat for stale detection).
  - the KPI framework never fabricates time series; it only uses user data.
  - decision ranking must state its cost/ROI assumptions explicitly.
  - a critique stage red-teams every recommendation against the evidence.
  - stages whose artifacts already exist are skipped, so the start endpoint
    can resume a failed run from the stage that broke instead of from zero.

All LLM calls use AsyncOpenAI + response_format=json_object + strict prompts.
"""
import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from openai import AsyncOpenAI

from src.bounce_board import kb, store
from src.bounce_board.constants import (
    AGENT_BY_INDUSTRY,
    FRAMEWORK_IDS,
    FRAMEWORK_LABELS,
    INDUSTRIES,
    STAGE_DETAILS,
    default_board,
)
from src.config import settings
from src.core.database import db

logger = logging.getLogger(__name__)

# Guards against double-running a session's pipeline (double click / re-entry).
_running: set[str] = set()

# How much attachment text is handed to the LLM stages, total.
_ATTACHMENT_DIGEST_CHARS = 24_000


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Usage:
    """Accumulates token usage across the run; attached to the final report."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.llm_calls = 0

    def add(self, resp: Any) -> None:
        usage = getattr(resp, "usage", None)
        self.llm_calls += 1
        if usage:
            self.prompt_tokens += usage.prompt_tokens or 0
            self.completion_tokens += usage.completion_tokens or 0

    def to_dict(self) -> dict:
        return {
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "llmCalls": self.llm_calls,
        }


async def _llm_json(
    client: AsyncOpenAI,
    system: str,
    payload: Any,
    usage: Optional[_Usage] = None,
    retries: int = 1,
) -> dict:
    """One json_object completion; retries once on bad JSON before raising."""
    last_error: Exception = RuntimeError("LLM call failed")
    for _ in range(retries + 1):
        try:
            resp = await client.chat.completions.create(
                model=settings.OPENAI_CHAT_MODEL,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, default=str)},
                ],
            )
            if usage:
                usage.add(resp)
            return json.loads(resp.choices[0].message.content or "{}")
        except Exception as e:  # noqa: BLE001 — retried, then surfaced to the stage
            last_error = e
            logger.warning("Bounce board LLM call failed, retrying: %s", e)
    raise last_error


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")[:40]


# ---------------------------------------------------------------------------
# Stage bookkeeping
# ---------------------------------------------------------------------------


def _set_stage(session: dict, stage_id: str, status: str, detail: Optional[str] = None) -> dict:
    stages = []
    for s in session.get("stages") or store.fresh_stages():
        if s["id"] == stage_id:
            s = {**s, "status": status}
            if status == "running":
                s["startedAt"] = _now()
            if status in ("complete", "error"):
                s["completedAt"] = _now()
            if detail:
                s["detail"] = detail
        stages.append(s)
    return store.update_session(session["id"], {"stages": stages, "current_stage": stage_id})


def _stage_done(session: dict, stage_id: str) -> bool:
    return any(
        s.get("id") == stage_id and s.get("status") == "complete"
        for s in session.get("stages") or []
    )


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


def _attachments_digest(attachments: list[dict]) -> str:
    """Concatenated (capped) extracted text of all session attachments."""
    if not attachments:
        return ""
    per_file = max(2_000, _ATTACHMENT_DIGEST_CHARS // max(1, len(attachments)))
    parts = []
    for a in attachments:
        text = (a.get("text") or "").strip()
        if text:
            parts.append(f"=== {a.get('name')} ===\n{text[:per_file]}")
    return "\n\n".join(parts)[:_ATTACHMENT_DIGEST_CHARS]


def _intake_detail(input_camel: dict, attachments: list[dict], digest: str) -> str:
    kpis = [k for k in (input_camel.get("kpis") or []) if (k.get("name") or "").strip()]
    parts = [f"Parsed problem statement ({len(input_camel.get('problemStatement') or '')} chars)"]
    if attachments:
        parts.append(f"{len(attachments)} attachment(s), {len(digest)} chars extracted")
    if kpis:
        parts.append(f"{len(kpis)} KPI(s)")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Context detection
# ---------------------------------------------------------------------------

_CONTEXT_PROMPT = """You are the context-detection engine of an AI business analysis system.
Given a business problem statement (plus optional KPI data and excerpts of uploaded
documents), detect its context.

Return STRICT JSON:
{
  "industry": "shipping" | "healthcare" | "manufacturing" | "logistics",
  "industryLabel": "the SPECIFIC domain in 2-5 words, e.g. 'Cold-chain pharma distribution' — do NOT just repeat the bucket name",
  "industryScores": [{"industry": "shipping", "confidence": 0.0-1.0}, ... one entry for EACH of the four industries, summing to ~1.0],
  "department": "short department name, e.g. Fleet Operations",
  "problemType": "issue" | "idea" | "question" | "decision",
  "userRole": "the likely role of the person asking, e.g. Operations Manager",
  "keywords": ["5-8 short domain keywords found in the problem"],
  "summary": "one sentence describing the problem's operational context"
}
"industry" is the closest of the four canonical buckets (used for knowledge routing);
"industryLabel" is the real, specific domain of this problem."""


async def _detect_context(
    client: AsyncOpenAI, input_camel: dict, attachments_digest: str, usage: _Usage
) -> dict:
    data = await _llm_json(
        client,
        _CONTEXT_PROMPT,
        {
            "title": input_camel.get("title"),
            "problem": input_camel.get("problemStatement"),
            "kpis": input_camel.get("kpis") or [],
            "documentExcerpts": attachments_digest[:8_000] or None,
        },
        usage,
    )
    industry = data.get("industry") if data.get("industry") in INDUSTRIES else "manufacturing"
    scores_by_industry = {
        s.get("industry"): float(s.get("confidence") or 0)
        for s in (data.get("industryScores") or [])
        if s.get("industry") in INDUSTRIES
    }
    context = {
        "industry": industry,
        "industryLabel": (data.get("industryLabel") or "").strip() or industry,
        "industryScores": [
            {"industry": i, "confidence": round(scores_by_industry.get(i, 0.02), 2)}
            for i in INDUSTRIES
        ],
        "department": data.get("department") or "Operations",
        "problemType": data.get("problemType")
        if data.get("problemType") in ("issue", "idea", "question", "decision")
        else "issue",
        "userRole": data.get("userRole") or "Manager",
        "keywords": [str(k) for k in (data.get("keywords") or [])][:8],
        "summary": data.get("summary") or "",
    }
    # Manual overrides win when auto-detect is off.
    if not input_camel.get("autoDetect", True):
        if input_camel.get("industryHint") in INDUSTRIES:
            context["industry"] = input_camel["industryHint"]
            context["industryScores"] = [
                {"industry": i, "confidence": 0.94 if i == context["industry"] else 0.02}
                for i in INDUSTRIES
            ]
        if input_camel.get("department"):
            context["department"] = input_camel["department"]
        if input_camel.get("problemType"):
            context["problemType"] = input_camel["problemType"]
        if input_camel.get("userRole"):
            context["userRole"] = input_camel["userRole"]
    return context


# ---------------------------------------------------------------------------
# Board composition (dynamic agents)
# ---------------------------------------------------------------------------

_BOARD_PROMPT = """You are the board-composition engine of an AI business analysis system.
Given a business problem and its detected context, design the expert board that
will analyze and debate it. Do NOT use a generic cast — tailor every member's
focus to THIS problem.

Return STRICT JSON:
{
  "expert": {
    "name": "e.g. Cold-Chain Distribution Agent",
    "title": "short role title",
    "description": "2 sentences: what this domain expert specializes in, incl. the relevant regulations/benchmarks",
    "expertise": ["4-6 short expertise areas"],
    "industryLabel": "the specific domain"
  },
  "personas": [
    5 or 6 members. The FIRST must be a chair (CEO-type) who drives to a decision.
    Include a domain expert persona and a risk/compliance persona. Each:
    {"id": "short-kebab-slug e.g. cfo / clinical-quality-expert",
     "name": "realistic full name",
     "title": "e.g. CFO Agent / Clinical Quality Expert",
     "focus": "2-3 sentences: their lens ON THIS PROBLEM — what they will push on, question and demand"}
  ],
  "rounds": [
    exactly 3: {"round": 1, "topic": "short topic tailored to the problem", "speakers": ["persona ids", 3 per round]}
    Round 3 must converge on a decision and its last speaker must be the chair.
  ],
  "rationale": "1-2 sentences on why this board fits the problem"
}"""


def _validate_board(data: dict, context: dict, has_kpis: bool) -> dict:
    """Sanitize the composed board; raise if it is unusable (caller falls back)."""
    expert_raw = data.get("expert") or {}
    expert = {
        "id": _slug(expert_raw.get("name") or "domain-expert") or "domain-expert",
        "name": expert_raw.get("name") or "Domain Expert Agent",
        "title": expert_raw.get("title") or "Domain Expert",
        "description": expert_raw.get("description") or "",
        "expertise": [str(e) for e in (expert_raw.get("expertise") or [])][:6],
        "industryLabel": expert_raw.get("industryLabel")
        or context.get("industryLabel")
        or context["industry"],
    }

    personas = []
    seen_ids: set[str] = set()
    for p in data.get("personas") or []:
        pid = _slug(p.get("id") or p.get("title") or "")
        if not pid or pid in seen_ids or not (p.get("name") and p.get("title")):
            continue
        seen_ids.add(pid)
        personas.append(
            {
                "id": pid,
                "name": str(p["name"]),
                "title": str(p["title"]),
                "focus": str(p.get("focus") or ""),
            }
        )
    if len(personas) < 4:
        raise ValueError(f"Board composition returned only {len(personas)} usable personas")
    personas = personas[:6]
    valid_ids = {p["id"] for p in personas}
    chair_id = personas[0]["id"]

    rounds = []
    for i, r in enumerate((data.get("rounds") or [])[:3]):
        speakers = [s for s in (r.get("speakers") or []) if s in valid_ids][:3]
        if not speakers:
            raise ValueError("Board composition returned a round with no valid speakers")
        rounds.append(
            {"round": i + 1, "topic": str(r.get("topic") or f"Round {i + 1}"), "speakers": speakers}
        )
    if len(rounds) != 3:
        raise ValueError("Board composition did not return 3 rounds")
    # The decision round always ends with the chair.
    if rounds[-1]["speakers"][-1] != chair_id:
        rounds[-1]["speakers"] = (rounds[-1]["speakers"] + [chair_id])[-3:]

    return {
        "expert": expert,
        "personas": personas,
        "rounds": rounds,
        # All frameworks always run; KPI only when the user provided KPI data
        # (running it without inputs would mean fabricating numbers).
        "frameworks": [f for f in FRAMEWORK_IDS if f != "kpi" or has_kpis],
        "rationale": str(data.get("rationale") or ""),
    }


async def _compose_board(
    client: AsyncOpenAI,
    problem: str,
    context: dict,
    input_camel: dict,
    attachments_digest: str,
    usage: _Usage,
) -> dict:
    has_kpis = bool([k for k in (input_camel.get("kpis") or []) if (k.get("name") or "").strip()])
    try:
        data = await _llm_json(
            client,
            _BOARD_PROMPT,
            {
                "problem": problem,
                "context": context,
                "kpisProvided": has_kpis,
                "kpis": input_camel.get("kpis") or [],
                "documentExcerpts": attachments_digest[:4_000] or None,
            },
            usage,
        )
        return _validate_board(data, context, has_kpis)
    except Exception:  # noqa: BLE001 — composition must never kill the run
        logger.exception("Board composition failed — using the default board")
        board = default_board(context["industry"])
        if not has_kpis:
            board["frameworks"] = [f for f in board["frameworks"] if f != "kpi"]
        elif "kpi" not in board["frameworks"]:
            board["frameworks"].append("kpi")
        return board


# ---------------------------------------------------------------------------
# Framework analysis
# ---------------------------------------------------------------------------

_FRAMEWORK_PROMPTS = {
    "five_whys": """Run a "Take 5" (5 Whys) root-cause analysis on the given business problem.
Ground every answer in the provided problem, documents and knowledge sources.
Return STRICT JSON:
{"problem": "one sentence restating the problem",
 "whys": [{"question": "Why ...?", "answer": "..."} x exactly 5],
 "rootCause": "one-sentence root cause"}""",
    "root_cause": """Run a root-cause (fishbone) analysis on the given business problem.
Ground causes in the provided problem, documents and knowledge sources.
Return STRICT JSON:
{"categories": [{"name": "Process|People|Systems|Equipment|Data|External (pick 4-5 relevant)", "causes": ["...", "..."]}],
 "primaryCause": "one sentence naming the single dominant cause"}""",
    "swot": """Run a SWOT analysis for the organization facing the given business problem.
Return STRICT JSON:
{"strengths": ["3 items"], "weaknesses": ["3 items"], "opportunities": ["3 items"], "threats": ["3 items"]}""",
    "gap": """Run a gap analysis for the given business problem.
Return STRICT JSON:
{"rows": [{"area": "...", "current": "...", "target": "...", "gap": "...",
           "priority": "low"|"medium"|"high"|"critical"} x 4-5]}""",
    "risk": """Run a risk analysis for addressing the given business problem.
Return STRICT JSON:
{"risks": [{"id": "risk-1", "title": "...", "likelihood": 1-5, "impact": 1-5,
            "severity": "low"|"medium"|"high"|"critical", "mitigation": "..."} x 3-4]}""",
    "kpi": """Produce a KPI analysis for the given business problem.
Use ONLY the KPI inputs provided (their names/current/target/unit EXACTLY).
Do NOT invent historical data: "series" must be [] unless the problem statement
or documents contain real past values, in which case chart those real points
(label each with its stated period).
Return STRICT JSON:
{"metrics": [{"name": "...", "unit": "...", "current": <number>, "target": <number>,
              "assessment": "improving"|"worsening"|"flat"|"unknown"  // direction relative to target, from the evidence,
              "note": "one sentence on what the gap means for this problem",
              "series": [{"label": "...", "value": <number>}] or []}]}""",
}


async def _run_framework(
    client: AsyncOpenAI, framework: str, payload: dict, usage: _Usage
) -> dict:
    data = await _llm_json(client, _FRAMEWORK_PROMPTS[framework], payload, usage)
    data["framework"] = framework
    return data


def _frameworks_digest(frameworks: list[dict]) -> str:
    """Compact text digest of the framework outputs for downstream prompts."""
    parts = []
    for f in frameworks:
        kind = f.get("framework")
        if kind == "five_whys":
            parts.append(f"Root cause (5 whys): {f.get('rootCause')}")
        elif kind == "root_cause":
            parts.append(f"Primary cause: {f.get('primaryCause')}")
        elif kind == "swot":
            parts.append(
                "SWOT — strengths: " + "; ".join(f.get("strengths") or [])
                + " | weaknesses: " + "; ".join(f.get("weaknesses") or [])
                + " | opportunities: " + "; ".join(f.get("opportunities") or [])
                + " | threats: " + "; ".join(f.get("threats") or [])
            )
        elif kind == "gap":
            rows = f.get("rows") or []
            parts.append("Gaps: " + "; ".join(f"{r.get('area')}: {r.get('gap')}" for r in rows))
        elif kind == "risk":
            risks = f.get("risks") or []
            parts.append("Risks: " + "; ".join(f"{r.get('title')} ({r.get('severity')})" for r in risks))
        elif kind == "kpi":
            metrics = f.get("metrics") or []
            parts.append(
                "KPIs: "
                + "; ".join(
                    f"{m.get('name')} {m.get('current')}{m.get('unit')} vs target "
                    f"{m.get('target')}{m.get('unit')} ({m.get('assessment') or 'unknown'})"
                    for m in metrics
                )
            )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Board discussion
# ---------------------------------------------------------------------------

_PERSONA_PROMPT = """You are {name}, {title}, on an AI executive "bounce board" reviewing a business problem.
Your lens: {focus}

You are speaking in round {round} ("{topic}") of a {total_rounds}-round board discussion.
Speak as a sharp executive: 2-4 sentences, concrete, referencing the analysis and
(where relevant) the knowledge base sources by name. Bold at most one key figure
or phrase with **markdown**. The final round must converge toward a decision.
Cite a source id ONLY if you actually used that source's content.

Return STRICT JSON:
{{"contentMd": "your message (markdown, 2-4 sentences)",
  "sentiment": "support" | "concern" | "question" | "neutral",
  "citationIds": ["kbIssueId of any source you referenced, else empty"]}}"""


def _sources_for_prompt(sources: list[dict]) -> list[dict]:
    return [
        {
            "kbIssueId": s["kbIssueId"],
            "title": s["title"],
            "excerpt": s["excerpt"],
            "content": (s.get("contentMd") or "")[:1_200],
        }
        for s in sources
    ]


async def _discussion_message(
    client: AsyncOpenAI,
    persona: dict,
    round_info: dict,
    total_rounds: int,
    problem: str,
    context: dict,
    sources: list[dict],
    frameworks_digest: str,
    prior_messages: list[dict],
    personas_by_id: dict,
    usage: _Usage,
) -> dict:
    system = _PERSONA_PROMPT.format(
        name=persona["name"],
        title=persona["title"],
        focus=persona["focus"],
        round=round_info["round"],
        topic=round_info["topic"],
        total_rounds=total_rounds,
    )
    data = await _llm_json(
        client,
        system,
        {
            "problem": problem,
            "context": context,
            "sources": _sources_for_prompt(sources),
            "analysis_digest": frameworks_digest,
            "discussion_so_far": [
                {
                    "speaker": personas_by_id.get(m["agentId"], {}).get("title", m["agentId"]),
                    "message": m["contentMd"],
                }
                for m in prior_messages
            ],
        },
        usage,
    )
    cited_ids = set(data.get("citationIds") or [])
    citations = [
        {k: v for k, v in s.items() if k != "contentMd"}
        for s in sources
        if s["kbIssueId"] in cited_ids
    ][:2]
    sentiment = data.get("sentiment")
    if sentiment not in ("support", "concern", "question", "neutral"):
        sentiment = "neutral"
    return {
        "id": f"msg-{uuid.uuid4().hex[:10]}",
        "agentId": persona["id"],
        "round": round_info["round"],
        "roundTopic": round_info["topic"],
        "contentMd": data.get("contentMd") or "…",
        "sentiment": sentiment,
        "citations": citations,
        "timestamp": _now(),
    }


# ---------------------------------------------------------------------------
# Decision ranking
# ---------------------------------------------------------------------------

_DECISION_PROMPT = """You are the decision engine of an AI executive board. Given the problem, analysis
and the board's discussion, produce 3-5 ranked recommendations.

Score each dimension 0-100 where HIGHER IS BETTER:
impact (business impact), cost (cost-friendliness: cheap=high), risk (risk-friendliness: safe=high),
urgency, feasibility.

All financial figures are ESTIMATES and must follow from explicitly stated
assumptions — list them. If the input gives no basis for a figure, choose a
conservative one and say so in the assumptions.

The valid board member ids are provided in the payload as "boardMemberIds";
supportingAgents/dissentingAgents must use ONLY those ids and reflect who
actually supported/objected in the discussion.

Return STRICT JSON:
{"recommendations": [{
  "title": "...", "description": "2-3 sentences",
  "category": "one word, e.g. Operations/Process/Compliance/Technology",
  "scores": {"impact": 0-100, "cost": 0-100, "risk": 0-100, "urgency": 0-100, "feasibility": 0-100},
  "estimatedCost": <number, USD>, "estimatedRoiPct": <number, 0 if compliance-only>,
  "effortWeeks": <int>,
  "assumptions": ["2-4 explicit assumptions the cost/ROI estimates rest on"],
  "supportingAgents": ["board member ids"],
  "dissentingAgents": ["board member ids, may be empty"],
  "citationIds": ["kbIssueId of grounding sources"]
}]}
Order by overall merit, best first."""


async def _rank_decisions(
    client: AsyncOpenAI,
    problem: str,
    context: dict,
    sources: list[dict],
    frameworks_digest: str,
    discussion: list[dict],
    board: dict,
    usage: _Usage,
) -> list[dict]:
    valid_agents = {p["id"] for p in board["personas"]}
    data = await _llm_json(
        client,
        _DECISION_PROMPT,
        {
            "problem": problem,
            "context": context,
            "analysis_digest": frameworks_digest,
            "boardMemberIds": sorted(valid_agents),
            "discussion": [
                {"speaker": m["agentId"], "sentiment": m["sentiment"], "message": m["contentMd"]}
                for m in discussion
            ],
            "sources": [{"kbIssueId": s["kbIssueId"], "title": s["title"]} for s in sources],
        },
        usage,
    )
    recommendations = []
    for i, raw in enumerate((data.get("recommendations") or [])[:5]):
        raw_scores = raw.get("scores") or {}
        scores = {
            "impact": _clamp(raw_scores.get("impact"), 0, 100, 60),
            "cost": _clamp(raw_scores.get("cost"), 0, 100, 60),
            "risk": _clamp(raw_scores.get("risk"), 0, 100, 60),
            "urgency": _clamp(raw_scores.get("urgency"), 0, 100, 60),
            "feasibility": _clamp(raw_scores.get("feasibility"), 0, 100, 60),
        }
        # Deterministic composite: impact & feasibility weighted heaviest.
        composite = round(
            0.3 * scores["impact"]
            + 0.15 * scores["cost"]
            + 0.15 * scores["risk"]
            + 0.15 * scores["urgency"]
            + 0.25 * scores["feasibility"]
        )
        cited = set(raw.get("citationIds") or [])
        recommendations.append(
            {
                "id": f"rec-{uuid.uuid4().hex[:8]}",
                "rank": i + 1,
                "title": raw.get("title") or f"Recommendation {i + 1}",
                "description": raw.get("description") or "",
                "category": raw.get("category") or "General",
                "scores": scores,
                "compositeScore": composite,
                "estimatedCost": float(raw.get("estimatedCost") or 0),
                "estimatedRoiPct": float(raw.get("estimatedRoiPct") or 0),
                "effortWeeks": _clamp(raw.get("effortWeeks"), 1, 52, 8),
                "assumptions": [str(a) for a in (raw.get("assumptions") or [])][:4],
                "supportingAgents": [a for a in (raw.get("supportingAgents") or []) if a in valid_agents],
                "dissentingAgents": [a for a in (raw.get("dissentingAgents") or []) if a in valid_agents],
                "sourceRefs": [
                    {k: v for k, v in s.items() if k != "contentMd"}
                    for s in sources
                    if s["kbIssueId"] in cited
                ][:3],
            }
        )
    recommendations.sort(key=lambda r: r["compositeScore"], reverse=True)
    for i, rec in enumerate(recommendations):
        rec["rank"] = i + 1
    return recommendations


# ---------------------------------------------------------------------------
# Critique (red team)
# ---------------------------------------------------------------------------

_CRITIQUE_PROMPT = """You are the red-team critic of an AI executive board. Your job is to CHALLENGE
each recommendation, not to endorse it. For every recommendation, check:
  1. Does it actually address the identified root cause?
  2. Is it grounded in the provided evidence (problem, documents, knowledge sources)
     or is it a generic consultancy answer?
  3. Are its cost/ROI assumptions plausible?

Return STRICT JSON:
{"critiques": [{
  "index": <0-based index of the recommendation>,
  "verdict": "grounded"   // addresses the root cause and is supported by the evidence
           | "weak"       // plausible but partially unsupported — say what is missing
           | "rejected",  // does not address the root cause or contradicts the evidence
  "note": "1-2 blunt sentences justifying the verdict"
}]}
One critique per recommendation. Be skeptical; "grounded" must be earned."""


async def _critique_recommendations(
    client: AsyncOpenAI,
    problem: str,
    frameworks_digest: str,
    sources: list[dict],
    recommendations: list[dict],
    usage: _Usage,
) -> list[dict]:
    data = await _llm_json(
        client,
        _CRITIQUE_PROMPT,
        {
            "problem": problem,
            "analysis_digest": frameworks_digest,
            "sources": _sources_for_prompt(sources),
            "recommendations": [
                {
                    "index": i,
                    "title": r["title"],
                    "description": r["description"],
                    "estimatedCost": r["estimatedCost"],
                    "estimatedRoiPct": r["estimatedRoiPct"],
                    "assumptions": r.get("assumptions") or [],
                }
                for i, r in enumerate(recommendations)
            ],
        },
        usage,
    )
    by_index: dict[int, dict] = {}
    for c in data.get("critiques") or []:
        idx = c.get("index")
        verdict = c.get("verdict")
        if isinstance(idx, int) and 0 <= idx < len(recommendations) and verdict in (
            "grounded",
            "weak",
            "rejected",
        ):
            by_index[idx] = {"verdict": verdict, "note": str(c.get("note") or "")}
    return [
        {**r, "critique": by_index.get(i, {"verdict": "weak", "note": "Not reviewed by the critic."})}
        for i, r in enumerate(recommendations)
    ]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_REPORT_PROMPT = """You are the report writer of an AI executive board. Compile a management report
from the problem, analysis, board discussion and final ranked recommendations
(each carries a red-team critique — reflect challenged items honestly).

All financial figures are estimates; costRoi.assumptions must state what they
rest on. Keep costs consistent with the recommendations' estimatedCost values.

Return STRICT JSON:
{"executiveSummaryMd": "markdown starting with '## Executive Summary', ~150-220 words, bold key figures",
 "actionPlan": [{"id": "act-1", "title": "...", "ownerRole": "...", "phase": "...",
                 "startWeek": <int>=1, "durationWeeks": <int>=1, "dependsOn": ["act-ids or empty"]} x 5-7],
 "phases": [{"name": "...", "startWeek": <int>, "endWeek": <int>} x 3-4  // must cover the action plan],
 "costRoi": {"totalCost": <number>, "expectedRoiPct": <number>, "paybackMonths": <int>,
             "breakdown": [{"label": "...", "amount": <number>} x 3-5],
             "assumptions": ["2-4 explicit assumptions behind these figures"]},
 "riskRegister": [{"id": "rr-1", "title": "...", "severity": "low"|"medium"|"high"|"critical",
                   "owner": "role", "mitigation": "...", "status": "open"|"mitigating"} x 3-4],
 "managementReportMd": "markdown starting with '### Management Report' with **Situation**, **Decision**, **Governance** paragraphs"}"""


async def _generate_report(
    client: AsyncOpenAI,
    session_id: str,
    problem: str,
    context: dict,
    frameworks_digest: str,
    discussion: list[dict],
    recommendations: list[dict],
    usage: _Usage,
) -> dict:
    data = await _llm_json(
        client,
        _REPORT_PROMPT,
        {
            "problem": problem,
            "context": context,
            "analysis_digest": frameworks_digest,
            "discussion_summary": [m["contentMd"] for m in discussion[-4:]],
            "recommendations": [
                {
                    "title": r["title"],
                    "compositeScore": r["compositeScore"],
                    "estimatedCost": r["estimatedCost"],
                    "estimatedRoiPct": r["estimatedRoiPct"],
                    "effortWeeks": r["effortWeeks"],
                    "assumptions": r.get("assumptions") or [],
                    "critique": r.get("critique"),
                }
                for r in recommendations
            ],
        },
        usage,
    )
    cost_roi = data.get("costRoi") or {}
    return {
        "sessionId": session_id,
        "generatedAt": _now(),
        "executiveSummaryMd": data.get("executiveSummaryMd") or "## Executive Summary\n\n(unavailable)",
        "recommendations": recommendations,
        "actionPlan": data.get("actionPlan") or [],
        "phases": data.get("phases") or [],
        "costRoi": {
            "totalCost": float(cost_roi.get("totalCost") or 0),
            "expectedRoiPct": float(cost_roi.get("expectedRoiPct") or 0),
            "paybackMonths": _clamp(cost_roi.get("paybackMonths"), 0, 120, 0),
            "breakdown": cost_roi.get("breakdown") or [],
            "assumptions": [str(a) for a in (cost_roi.get("assumptions") or [])][:4],
        },
        "riskRegister": data.get("riskRegister") or [],
        "managementReportMd": data.get("managementReportMd") or "### Management Report\n\n(unavailable)",
        "tokenUsage": usage.to_dict(),
    }


# ---------------------------------------------------------------------------
# Ask the board (post-analysis follow-up)
# ---------------------------------------------------------------------------

_FOLLOWUP_PROMPT = """You are {name}, {title}, on an AI executive "bounce board" that has just completed
its analysis of a business problem. Your lens: {focus}

The user now asks you a follow-up question. Answer it directly in 2-5 sentences,
grounded in the analysis, the discussion and the knowledge sources — do not
restart the analysis. Bold at most one key figure or phrase.

Return STRICT JSON:
{{"contentMd": "your answer (markdown)",
  "sentiment": "support" | "concern" | "question" | "neutral",
  "citationIds": ["kbIssueId of any source you referenced, else empty"]}}"""


async def ask_board(session_row: dict, question: str, persona_id: Optional[str]) -> dict:
    """Answer a follow-up question as one board persona and append it to the
    session's discussion (published to the WebSocket via update_session)."""
    board = session_row.get("board") or default_board(
        (session_row.get("context") or {}).get("industry") or "manufacturing"
    )
    personas_by_id = {p["id"]: p for p in board["personas"]}
    persona = personas_by_id.get(persona_id or "") or board["personas"][0]

    discussion = session_row.get("discussion") or {"status": "complete", "messages": []}
    messages = list(discussion.get("messages") or [])
    max_round = max((m.get("round") or 0 for m in messages), default=0)

    problem = (session_row.get("input") or {}).get("problemStatement") or session_row.get("title") or ""
    sources = session_row.get("retrieved_sources") or []
    digest = _frameworks_digest(session_row.get("frameworks") or [])
    recommendations = session_row.get("recommendations") or []

    usage = _Usage()
    client = _client()
    system = _FOLLOWUP_PROMPT.format(
        name=persona["name"], title=persona["title"], focus=persona["focus"]
    )
    data = await _llm_json(
        client,
        system,
        {
            "problem": problem,
            "question": question,
            "analysis_digest": digest,
            "recommendations": [
                {"title": r["title"], "compositeScore": r["compositeScore"]}
                for r in recommendations
            ],
            "sources": _sources_for_prompt(sources),
            "recent_discussion": [
                {
                    "speaker": personas_by_id.get(m["agentId"], {}).get("title", m["agentId"]),
                    "message": m["contentMd"],
                }
                for m in messages[-6:]
            ],
        },
        usage,
    )
    cited_ids = set(data.get("citationIds") or [])
    sentiment = data.get("sentiment")
    if sentiment not in ("support", "concern", "question", "neutral"):
        sentiment = "neutral"
    message = {
        "id": f"msg-{uuid.uuid4().hex[:10]}",
        "agentId": persona["id"],
        "round": max_round + 1,
        "roundTopic": "Follow-up",
        "contentMd": data.get("contentMd") or "…",
        "sentiment": sentiment,
        "citations": [
            {k: v for k, v in s.items() if k != "contentMd"}
            for s in sources
            if s["kbIssueId"] in cited_ids
        ][:2],
        "timestamp": _now(),
    }
    messages.append(message)
    store.update_session(
        session_row["id"],
        {"discussion": {"status": "complete", "activeAgentId": None, "messages": messages}},
    )
    return message


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def start(session_id: str) -> None:
    """Fire-and-forget the pipeline for a session (idempotent per session)."""
    if session_id in _running:
        return
    _running.add(session_id)
    asyncio.create_task(_run(session_id))


async def _run(session_id: str) -> None:
    client = _client()
    usage = _Usage()
    try:
        # The start endpoint already reset the row (fully or from a stage); re-read it.
        result = (
            db.admin.table(store.TABLE).select("*").eq("id", session_id).single().execute()
        )
        session = result.data
        input_camel = session.get("input") or {}
        problem = input_camel.get("problemStatement") or session.get("title") or ""
        attachments = session.get("attachments") or []
        att_digest = _attachments_digest(attachments)
        has_kpis = bool(
            [k for k in (input_camel.get("kpis") or []) if (k.get("name") or "").strip()]
        )

        # 1. intake — parse what we actually have and say so.
        if not _stage_done(session, "intake"):
            session = _set_stage(session, "intake", "running")
            session = _set_stage(
                session, "intake", "complete", _intake_detail(input_camel, attachments, att_digest)
            )

        # 2. context detection
        if _stage_done(session, "context_detection") and session.get("context"):
            context = session["context"]
        else:
            session = _set_stage(session, "context_detection", "running")
            context = await _detect_context(client, input_camel, att_digest, usage)
            session = store.update_session(session_id, {"context": context})
            session = _set_stage(
                session, "context_detection", "complete", STAGE_DETAILS["context_detection"]
            )

        # 3. board composition (dynamic agents)
        if _stage_done(session, "agent_routing") and session.get("board"):
            board = session["board"]
        else:
            session = _set_stage(session, "agent_routing", "running")
            board = await _compose_board(client, problem, context, input_camel, att_digest, usage)
            legacy_agent = AGENT_BY_INDUSTRY.get(context["industry"])
            session = store.update_session(
                session_id,
                {"board": board, "routed_agent_id": legacy_agent["id"] if legacy_agent else None},
            )
            session = _set_stage(
                session,
                "agent_routing",
                "complete",
                f"Composed {board['expert']['name']} + {len(board['personas'])}-member board",
            )
        personas_by_id = {p["id"]: p for p in board["personas"]}

        # 4. knowledge retrieval (org-scoped)
        if _stage_done(session, "knowledge_retrieval") and session.get("retrieved_sources") is not None:
            sources = session["retrieved_sources"]
        else:
            session = _set_stage(session, "knowledge_retrieval", "running")
            sources = await kb.search(
                problem,
                industry=context["industry"],
                top_k=5,
                org_id=session.get("org_id"),
            )
            session = store.update_session(session_id, {"retrieved_sources": sources})
            session = _set_stage(
                session,
                "knowledge_retrieval",
                "complete",
                f"Retrieved {len(sources)} relevant source(s)"
                if sources
                else "No sufficiently relevant knowledge base sources found",
            )

        # 5. framework analysis — only the selected frameworks, written as each
        #    completes (live UI + keeps updated_at fresh for stale detection).
        if _stage_done(session, "framework_analysis") and session.get("frameworks"):
            frameworks = session["frameworks"]
        else:
            session = _set_stage(session, "framework_analysis", "running")
            # Every framework runs on every problem; KPI needs user KPI data.
            selected = [f for f in FRAMEWORK_IDS if f != "kpi" or has_kpis]
            fw_payload = {
                "problem": problem,
                "context": context,
                "kpis": input_camel.get("kpis") or [],
                "documentExcerpts": att_digest[:8_000] or None,
                "knowledge": [
                    {
                        "title": s["title"],
                        "excerpt": s["excerpt"],
                        "content": (s.get("contentMd") or "")[:1_200],
                    }
                    for s in sources
                ],
            }
            frameworks = []
            tasks = [
                asyncio.create_task(_run_framework(client, fw, fw_payload, usage))
                for fw in selected
            ]
            for coro in asyncio.as_completed(tasks):
                frameworks.append(await coro)
                order = {fw: i for i, fw in enumerate(selected)}
                frameworks.sort(key=lambda f: order.get(f.get("framework"), 99))
                session = store.update_session(session_id, {"frameworks": frameworks})
            session = _set_stage(
                session,
                "framework_analysis",
                "complete",
                "Ran " + ", ".join(FRAMEWORK_LABELS.get(f, f) for f in selected)
                + ("" if has_kpis else " (add KPI data to include KPI analysis)"),
            )
        digest = _frameworks_digest(frameworks)

        # 6. board discussion — stream messages onto the row one by one
        if _stage_done(session, "board_discussion") and (session.get("discussion") or {}).get(
            "messages"
        ):
            messages = session["discussion"]["messages"]
        else:
            session = _set_stage(session, "board_discussion", "running")
            rounds = board["rounds"]
            speakers = [(r, pid) for r in rounds for pid in r["speakers"] if pid in personas_by_id]
            messages = []
            discussion = {
                "status": "streaming",
                "activeAgentId": speakers[0][1] if speakers else None,
                "messages": [],
            }
            store.update_session(session_id, {"discussion": discussion})
            for idx, (round_info, persona_id) in enumerate(speakers):
                message = await _discussion_message(
                    client,
                    personas_by_id[persona_id],
                    round_info,
                    len(rounds),
                    problem,
                    context,
                    sources,
                    digest,
                    messages,
                    personas_by_id,
                    usage,
                )
                messages.append(message)
                next_speaker = speakers[idx + 1][1] if idx + 1 < len(speakers) else None
                discussion = {"status": "streaming", "activeAgentId": next_speaker, "messages": messages}
                store.update_session(session_id, {"discussion": discussion})
            discussion = {"status": "complete", "activeAgentId": None, "messages": messages}
            store.update_session(session_id, {"discussion": discussion})
            session = _set_stage(
                session, "board_discussion", "complete", STAGE_DETAILS["board_discussion"]
            )

        # 7. decision ranking
        if _stage_done(session, "decision_ranking") and session.get("recommendations"):
            recommendations = session["recommendations"]
        else:
            session = _set_stage(session, "decision_ranking", "running")
            recommendations = await _rank_decisions(
                client, problem, context, sources, digest, messages, board, usage
            )
            session = store.update_session(session_id, {"recommendations": recommendations})
            session = _set_stage(
                session, "decision_ranking", "complete", STAGE_DETAILS["decision_ranking"]
            )

        # 8. critique — red-team every recommendation
        if not _stage_done(session, "critique"):
            session = _set_stage(session, "critique", "running")
            try:
                recommendations = await _critique_recommendations(
                    client, problem, digest, sources, recommendations, usage
                )
                session = store.update_session(session_id, {"recommendations": recommendations})
                grounded = sum(
                    1 for r in recommendations if (r.get("critique") or {}).get("verdict") == "grounded"
                )
                detail = f"{grounded}/{len(recommendations)} recommendations verified as grounded"
            except Exception:  # noqa: BLE001 — critique is additive, never fatal
                logger.exception("Critique stage failed for session %s", session_id)
                detail = "Critique unavailable — recommendations shown unverified"
            session = _set_stage(session, "critique", "complete", detail)

        # 9. report
        if not (_stage_done(session, "report_generation") and session.get("report")):
            session = _set_stage(session, "report_generation", "running")
            report = await _generate_report(
                client, session_id, problem, context, digest, messages, recommendations, usage
            )
            session = store.update_session(session_id, {"report": report})
            session = _set_stage(
                session, "report_generation", "complete", STAGE_DETAILS["report_generation"]
            )

        # 10. done
        session = _set_stage(session, "complete", "complete")
        store.update_session(session_id, {"status": "complete"})
        logger.info(
            "Bounce board pipeline complete for session %s (%s LLM calls, %s prompt / %s completion tokens)",
            session_id,
            usage.llm_calls,
            usage.prompt_tokens,
            usage.completion_tokens,
        )
    except Exception:  # noqa: BLE001 — any failure marks the session errored
        logger.exception("Bounce board pipeline failed for session %s", session_id)
        try:
            result = (
                db.admin.table(store.TABLE).select("stages").eq("id", session_id).single().execute()
            )
            stages = [
                {**s, "status": "error", "detail": "This stage failed — retry from here or rerun."}
                if s.get("status") == "running"
                else s
                for s in (result.data.get("stages") or [])
            ]
            store.update_session(session_id, {"status": "error", "stages": stages})
        except Exception:  # noqa: BLE001
            logger.exception("Failed to mark session %s as errored", session_id)
    finally:
        _running.discard(session_id)
