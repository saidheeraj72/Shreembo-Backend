"""
Bounce Board — the 9-stage analysis pipeline.

Launched fire-and-forget (asyncio.create_task) from the start endpoint; after
every stage it writes the artifact onto the session row, which the frontend
polls at 800ms. During board_discussion each persona message is appended to
the row as it is generated, so the UI streams the debate live.

All LLM calls use the repo pattern from src/email_agent/issues.py:
AsyncOpenAI + response_format=json_object + strict system prompt.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from openai import AsyncOpenAI

from src.bounce_board import kb, store
from src.bounce_board.constants import (
    AGENT_BY_INDUSTRY,
    DISCUSSION_ROUNDS,
    INDUSTRIES,
    PERSONAS,
    STAGE_DETAILS,
)
from src.config import settings
from src.core.database import db

logger = logging.getLogger(__name__)

# Guards against double-running a session's pipeline (double click / re-entry).
_running: set[str] = set()


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _llm_json(
    client: AsyncOpenAI,
    system: str,
    payload: Any,
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


# ---------------------------------------------------------------------------
# LLM stages
# ---------------------------------------------------------------------------

_CONTEXT_PROMPT = """You are the context-detection engine of an AI business analysis system.
Given a business problem statement (and optional KPI data), detect its context.

Return STRICT JSON:
{
  "industry": "shipping" | "healthcare" | "manufacturing" | "logistics",
  "industryScores": [{"industry": "shipping", "confidence": 0.0-1.0}, ... one entry for EACH of the four industries, summing to ~1.0],
  "department": "short department name, e.g. Fleet Operations",
  "problemType": "issue" | "idea" | "question" | "decision",
  "userRole": "the likely role of the person asking, e.g. Operations Manager",
  "keywords": ["5-8 short domain keywords found in the problem"],
  "summary": "one sentence describing the problem's operational context"
}
Pick the closest of the four industries even if imperfect."""


async def _detect_context(client: AsyncOpenAI, input_camel: dict) -> dict:
    data = await _llm_json(
        client,
        _CONTEXT_PROMPT,
        {
            "title": input_camel.get("title"),
            "problem": input_camel.get("problemStatement"),
            "kpis": input_camel.get("kpis") or [],
        },
    )
    industry = data.get("industry") if data.get("industry") in INDUSTRIES else "manufacturing"
    scores_by_industry = {
        s.get("industry"): float(s.get("confidence") or 0)
        for s in (data.get("industryScores") or [])
        if s.get("industry") in INDUSTRIES
    }
    context = {
        "industry": industry,
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


_FRAMEWORK_PROMPTS = {
    "five_whys": """Run a 5 Whys analysis on the given business problem.
Return STRICT JSON:
{"problem": "one sentence restating the problem",
 "whys": [{"question": "Why ...?", "answer": "..."} x exactly 5],
 "rootCause": "one-sentence root cause"}""",
    "root_cause": """Run a root-cause (fishbone) analysis on the given business problem.
Return STRICT JSON:
{"categories": [{"name": "Process|People|Systems|Equipment|Data|External (pick 4-5 relevant)", "causes": ["...", "..."]}],
 "primaryCause": "one sentence naming the single dominant cause"}""",
    "swot": """Run a SWOT analysis for the organization facing the given problem.
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
If KPI inputs are provided, use their names/current/target/unit exactly; otherwise
infer 3-4 KPIs that would describe this problem. For each metric synthesize a
plausible recent 4-point series ("labels" like months/quarters) consistent with
the current value and trend implied by the problem.
Return STRICT JSON:
{"metrics": [{"name": "...", "unit": "...", "current": <number>, "target": <number>,
              "trend": "up"|"down"|"flat",
              "series": [{"label": "...", "value": <number>} x 4]}]}""",
}


async def _run_framework(client: AsyncOpenAI, framework: str, payload: dict) -> dict:
    data = await _llm_json(client, _FRAMEWORK_PROMPTS[framework], payload)
    data["framework"] = framework
    return data


_PERSONA_PROMPT = """You are {name}, {title}, on an AI executive "bounce board" reviewing a business problem.
Your lens: {focus}

You are speaking in round {round} ("{topic}") of a 3-round board discussion.
Speak as a sharp executive: 2-4 sentences, concrete, referencing the analysis and
(where relevant) the knowledge base sources by name. Bold at most one key figure
or phrase with **markdown**. Round 3 must converge toward a decision.

Return STRICT JSON:
{{"contentMd": "your message (markdown, 2-4 sentences)",
  "sentiment": "support" | "concern" | "question" | "neutral",
  "citationIds": ["kbIssueId of any source you referenced, else empty"]}}"""


async def _discussion_message(
    client: AsyncOpenAI,
    persona_id: str,
    round_info: dict,
    problem: str,
    context: dict,
    sources: list[dict],
    frameworks_digest: str,
    prior_messages: list[dict],
) -> dict:
    persona = PERSONAS[persona_id]
    system = _PERSONA_PROMPT.format(
        name=persona["name"],
        title=persona["title"],
        focus=persona["focus"],
        round=round_info["round"],
        topic=round_info["topic"],
    )
    data = await _llm_json(
        client,
        system,
        {
            "problem": problem,
            "context": context,
            "sources": [
                {"kbIssueId": s["kbIssueId"], "title": s["title"], "excerpt": s["excerpt"]}
                for s in sources
            ],
            "analysis_digest": frameworks_digest,
            "discussion_so_far": [
                {"speaker": PERSONAS[m["agentId"]]["title"], "message": m["contentMd"]}
                for m in prior_messages
            ],
        },
    )
    cited_ids = set(data.get("citationIds") or [])
    citations = [s for s in sources if s["kbIssueId"] in cited_ids][:2]
    sentiment = data.get("sentiment")
    if sentiment not in ("support", "concern", "question", "neutral"):
        sentiment = "neutral"
    return {
        "id": f"msg-{uuid.uuid4().hex[:10]}",
        "agentId": persona_id,
        "round": round_info["round"],
        "roundTopic": round_info["topic"],
        "contentMd": data.get("contentMd") or "…",
        "sentiment": sentiment,
        "citations": citations,
        "timestamp": _now(),
    }


_DECISION_PROMPT = """You are the decision engine of an AI executive board. Given the problem, analysis
and the board's discussion, produce 3-5 ranked recommendations.

Score each dimension 0-100 where HIGHER IS BETTER:
impact (business impact), cost (cost-friendliness: cheap=high), risk (risk-friendliness: safe=high),
urgency, feasibility.

Return STRICT JSON:
{"recommendations": [{
  "title": "...", "description": "2-3 sentences",
  "category": "one word, e.g. Operations/Process/Compliance/Technology",
  "scores": {"impact": 0-100, "cost": 0-100, "risk": 0-100, "urgency": 0-100, "feasibility": 0-100},
  "estimatedCost": <number, USD>, "estimatedRoiPct": <number, 0 if compliance-only>,
  "effortWeeks": <int>,
  "supportingAgents": ["ceo"|"cfo"|"coo"|"cto"|"industry_expert"|"risk_expert", ...],
  "dissentingAgents": [...same ids, may be empty],
  "citationIds": ["kbIssueId of grounding sources"]
}]}
Order by overall merit, best first. Reflect who actually supported/objected in the discussion."""


async def _rank_decisions(
    client: AsyncOpenAI,
    problem: str,
    context: dict,
    sources: list[dict],
    frameworks_digest: str,
    discussion: list[dict],
) -> list[dict]:
    data = await _llm_json(
        client,
        _DECISION_PROMPT,
        {
            "problem": problem,
            "context": context,
            "analysis_digest": frameworks_digest,
            "discussion": [
                {"speaker": m["agentId"], "sentiment": m["sentiment"], "message": m["contentMd"]}
                for m in discussion
            ],
            "sources": [{"kbIssueId": s["kbIssueId"], "title": s["title"]} for s in sources],
        },
    )
    valid_agents = set(PERSONAS.keys())
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
                "supportingAgents": [a for a in (raw.get("supportingAgents") or []) if a in valid_agents],
                "dissentingAgents": [a for a in (raw.get("dissentingAgents") or []) if a in valid_agents],
                "sourceRefs": [s for s in sources if s["kbIssueId"] in cited][:3],
            }
        )
    recommendations.sort(key=lambda r: r["compositeScore"], reverse=True)
    for i, rec in enumerate(recommendations):
        rec["rank"] = i + 1
    return recommendations


_REPORT_PROMPT = """You are the report writer of an AI executive board. Compile a management report
from the problem, analysis, board discussion and final ranked recommendations.

Return STRICT JSON:
{"executiveSummaryMd": "markdown starting with '## Executive Summary', ~150-220 words, bold key figures",
 "actionPlan": [{"id": "act-1", "title": "...", "ownerRole": "...", "phase": "...",
                 "startWeek": <int>=1, "durationWeeks": <int>=1, "dependsOn": ["act-ids or empty"]} x 5-7],
 "phases": [{"name": "...", "startWeek": <int>, "endWeek": <int>} x 3-4  // must cover the action plan],
 "costRoi": {"totalCost": <number>, "expectedRoiPct": <number>, "paybackMonths": <int>,
             "breakdown": [{"label": "...", "amount": <number>} x 3-5]},
 "riskRegister": [{"id": "rr-1", "title": "...", "severity": "low"|"medium"|"high"|"critical",
                   "owner": "role", "mitigation": "...", "status": "open"|"mitigating"} x 3-4],
 "managementReportMd": "markdown starting with '### Management Report' with **Situation**, **Decision**, **Governance** paragraphs"}
Keep costs consistent with the recommendations' estimatedCost values."""


async def _generate_report(
    client: AsyncOpenAI,
    session_id: str,
    problem: str,
    context: dict,
    frameworks_digest: str,
    discussion: list[dict],
    recommendations: list[dict],
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
                }
                for r in recommendations
            ],
        },
    )
    return {
        "sessionId": session_id,
        "generatedAt": _now(),
        "executiveSummaryMd": data.get("executiveSummaryMd") or "## Executive Summary\n\n(unavailable)",
        "recommendations": recommendations,
        "actionPlan": data.get("actionPlan") or [],
        "phases": data.get("phases") or [],
        "costRoi": data.get("costRoi")
        or {"totalCost": 0, "expectedRoiPct": 0, "paybackMonths": 0, "breakdown": []},
        "riskRegister": data.get("riskRegister") or [],
        "managementReportMd": data.get("managementReportMd") or "### Management Report\n\n(unavailable)",
    }


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
                    f"{m.get('name')} {m.get('current')}{m.get('unit')} vs target {m.get('target')}{m.get('unit')}"
                    for m in metrics
                )
            )
    return "\n".join(parts)


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
    try:
        # The start endpoint already reset the row; re-read it.
        result = (
            db.admin.table(store.TABLE).select("*").eq("id", session_id).single().execute()
        )
        session = result.data
        input_camel = session.get("input") or {}
        problem = input_camel.get("problemStatement") or session.get("title") or ""

        # 1. intake
        session = _set_stage(session, "intake", "running")
        await asyncio.sleep(0.5)
        session = _set_stage(session, "intake", "complete", STAGE_DETAILS["intake"])

        # 2. context detection
        session = _set_stage(session, "context_detection", "running")
        context = await _detect_context(client, input_camel)
        session = store.update_session(session_id, {"context": context})
        session = _set_stage(session, "context_detection", "complete", STAGE_DETAILS["context_detection"])

        # 3. agent routing (deterministic)
        session = _set_stage(session, "agent_routing", "running")
        agent = AGENT_BY_INDUSTRY[context["industry"]]
        session = store.update_session(session_id, {"routed_agent_id": agent["id"]})
        session = _set_stage(session, "agent_routing", "complete", STAGE_DETAILS["agent_routing"])

        # 4. knowledge retrieval
        session = _set_stage(session, "knowledge_retrieval", "running")
        sources = await kb.search(problem, industry=context["industry"], top_k=5)
        session = store.update_session(session_id, {"retrieved_sources": sources})
        session = _set_stage(session, "knowledge_retrieval", "complete", STAGE_DETAILS["knowledge_retrieval"])

        # 5. framework analysis (6 concurrent LLM calls)
        session = _set_stage(session, "framework_analysis", "running")
        fw_payload = {
            "problem": problem,
            "context": context,
            "kpis": input_camel.get("kpis") or [],
            "knowledge": [{"title": s["title"], "excerpt": s["excerpt"]} for s in sources],
        }
        frameworks = list(
            await asyncio.gather(
                *[_run_framework(client, fw, fw_payload) for fw in _FRAMEWORK_PROMPTS]
            )
        )
        session = store.update_session(session_id, {"frameworks": frameworks})
        session = _set_stage(session, "framework_analysis", "complete", STAGE_DETAILS["framework_analysis"])
        digest = _frameworks_digest(frameworks)

        # 6. board discussion — stream messages onto the row one by one
        session = _set_stage(session, "board_discussion", "running")
        messages: list[dict] = []
        discussion = {"status": "streaming", "activeAgentId": DISCUSSION_ROUNDS[0]["speakers"][0], "messages": []}
        store.update_session(session_id, {"discussion": discussion})
        speakers = [(r, pid) for r in DISCUSSION_ROUNDS for pid in r["speakers"]]
        for idx, (round_info, persona_id) in enumerate(speakers):
            message = await _discussion_message(
                client, persona_id, round_info, problem, context, sources, digest, messages
            )
            messages.append(message)
            next_speaker = speakers[idx + 1][1] if idx + 1 < len(speakers) else None
            discussion = {"status": "streaming", "activeAgentId": next_speaker, "messages": messages}
            store.update_session(session_id, {"discussion": discussion})
        discussion = {"status": "complete", "activeAgentId": None, "messages": messages}
        store.update_session(session_id, {"discussion": discussion})
        session = _set_stage(session, "board_discussion", "complete", STAGE_DETAILS["board_discussion"])

        # 7. decision ranking
        session = _set_stage(session, "decision_ranking", "running")
        recommendations = await _rank_decisions(client, problem, context, sources, digest, messages)
        session = store.update_session(session_id, {"recommendations": recommendations})
        session = _set_stage(session, "decision_ranking", "complete", STAGE_DETAILS["decision_ranking"])

        # 8. report
        session = _set_stage(session, "report_generation", "running")
        report = await _generate_report(
            client, session_id, problem, context, digest, messages, recommendations
        )
        session = store.update_session(session_id, {"report": report})
        session = _set_stage(session, "report_generation", "complete", STAGE_DETAILS["report_generation"])

        # 9. done
        session = _set_stage(session, "complete", "complete")
        store.update_session(session_id, {"status": "complete"})
        logger.info("Bounce board pipeline complete for session %s", session_id)
    except Exception:  # noqa: BLE001 — any failure marks the session errored
        logger.exception("Bounce board pipeline failed for session %s", session_id)
        try:
            result = (
                db.admin.table(store.TABLE).select("stages").eq("id", session_id).single().execute()
            )
            stages = [
                {**s, "status": "error", "detail": "This stage failed — rerun the analysis."}
                if s.get("status") == "running"
                else s
                for s in (result.data.get("stages") or [])
            ]
            store.update_session(session_id, {"status": "error", "stages": stages})
        except Exception:  # noqa: BLE001
            logger.exception("Failed to mark session %s as errored", session_id)
    finally:
        _running.discard(session_id)
