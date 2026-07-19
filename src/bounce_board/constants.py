"""Bounce Board — industry agents, board personas and pipeline stage order."""

PIPELINE_STAGES = [
    "intake",
    "context_detection",
    "agent_routing",
    "knowledge_retrieval",
    "framework_analysis",
    "board_discussion",
    "decision_ranking",
    "critique",
    "report_generation",
    "complete",
]

STAGE_DETAILS = {
    "intake": "Parsed problem statement, attachments and KPI data",
    "context_detection": "Detected industry, department, problem type and role",
    "agent_routing": "Composed a problem-specific expert board",
    "knowledge_retrieval": "Searched regulations, SOPs, incidents and best practices",
    "framework_analysis": "Ran Take 5, Root Cause, SWOT, Gap, Risk and KPI analysis",
    "board_discussion": "AI executive board debated the findings",
    "decision_ranking": "Ranked recommendations by impact, cost, risk, urgency, feasibility",
    "critique": "Red-team pass challenged each recommendation against the evidence",
    "report_generation": "Compiled the management report",
}

FRAMEWORK_IDS = ["five_whys", "root_cause", "swot", "gap", "risk", "kpi"]

# Display names ("five_whys" is branded "Take 5" in the product).
FRAMEWORK_LABELS = {
    "five_whys": "Take 5",
    "root_cause": "Root Cause",
    "swot": "SWOT",
    "gap": "Gap Analysis",
    "risk": "Risk Matrix",
    "kpi": "KPI Analysis",
}

INDUSTRIES = ["shipping", "healthcare", "manufacturing", "logistics"]

# Mirrors the frontend fixtures (enterprise-intelligence-hub/src/lib/bounce-board-fixtures.ts).
INDUSTRY_AGENTS = [
    {
        "id": "agent-marine",
        "industry": "shipping",
        "name": "Marine Operations Agent",
        "description": (
            "Specialist in vessel operations, port logistics, charter economics, "
            "crew management and maritime compliance (IMO, SOLAS, MARPOL)."
        ),
        "expertise": ["Vessel ops", "Port turnaround", "Charter economics", "IMO compliance", "Bunker optimization"],
    },
    {
        "id": "agent-health",
        "industry": "healthcare",
        "name": "Healthcare Operations Agent",
        "description": (
            "Specialist in hospital throughput, clinical quality, staffing models, "
            "patient safety and healthcare regulation (HIPAA, JCI, NABH)."
        ),
        "expertise": ["Patient flow", "Clinical quality", "Staffing & rostering", "HIPAA / JCI", "Revenue cycle"],
    },
    {
        "id": "agent-mfg",
        "industry": "manufacturing",
        "name": "Manufacturing Excellence Agent",
        "description": (
            "Specialist in production planning, OEE, lean/TPM programs, quality "
            "systems (ISO 9001) and industrial safety (OSHA)."
        ),
        "expertise": ["OEE & downtime", "Lean / TPM", "Quality systems", "OSHA safety", "Supply planning"],
    },
    {
        "id": "agent-supply",
        "industry": "logistics",
        "name": "Supply Chain Agent",
        "description": (
            "Specialist in warehousing, last-mile delivery, inventory strategy, "
            "freight procurement and network design."
        ),
        "expertise": ["Warehouse ops", "Last-mile", "Inventory strategy", "Freight procurement", "Network design"],
    },
]

AGENT_BY_INDUSTRY = {a["industry"]: a for a in INDUSTRY_AGENTS}

PERSONAS = {
    "ceo": {
        "name": "Alexandra Reyes",
        "title": "CEO Agent",
        "focus": (
            "Strategy, growth and organizational alignment. You frame problems in "
            "business terms, push the board toward a clear decision and summarize consensus."
        ),
    },
    "cfo": {
        "name": "Marcus Chen",
        "title": "CFO Agent",
        "focus": (
            "Cost, cash flow, ROI and capital allocation. You interrogate the financial "
            "case, question payback assumptions and demand scope discipline."
        ),
    },
    "coo": {
        "name": "Priya Nair",
        "title": "COO Agent",
        "focus": (
            "Operations, execution and process discipline. You care about who owns what, "
            "sequencing, no-regret moves and execution risk."
        ),
    },
    "cto": {
        "name": "David Okafor",
        "title": "CTO Agent",
        "focus": (
            "Technology, data and automation leverage. You identify system gaps, "
            "integration opportunities and how to keep tooling scope small."
        ),
    },
    "industry_expert": {
        "name": "Ingrid Larsen",
        "title": "Industry Expert",
        "focus": (
            "Domain benchmarks, regulation and what top-quartile operators do. You cite "
            "the retrieved knowledge base sources and industry patterns."
        ),
    },
    "risk_expert": {
        "name": "Samir Haddad",
        "title": "Risk Expert",
        "focus": (
            "Risk exposure, compliance and mitigation. You flag what could go wrong, "
            "insist on sequencing and interim controls, and name conditions for approval."
        ),
    },
}

# 3 rounds x 3 speakers = 9 messages per discussion.
DISCUSSION_ROUNDS = [
    {"round": 1, "topic": "Framing the problem", "speakers": ["ceo", "industry_expert", "cfo"]},
    {"round": 2, "topic": "Solution shaping", "speakers": ["coo", "cto", "risk_expert"]},
    {"round": 3, "topic": "Decision round", "speakers": ["risk_expert", "cfo", "ceo"]},
]


def default_board(industry: str) -> dict:
    """Static fallback board (the pre-v2 fixed cast) used when dynamic board
    composition fails — the pipeline must never die because one LLM call did."""
    agent = AGENT_BY_INDUSTRY.get(industry) or AGENT_BY_INDUSTRY["manufacturing"]
    return {
        "expert": {
            "id": agent["id"],
            "name": agent["name"],
            "title": agent["name"],
            "description": agent["description"],
            "expertise": agent["expertise"],
            "industryLabel": industry,
        },
        "personas": [
            {"id": pid, "name": p["name"], "title": p["title"], "focus": p["focus"]}
            for pid, p in PERSONAS.items()
        ],
        "rounds": DISCUSSION_ROUNDS,
        "frameworks": [f for f in FRAMEWORK_IDS if f != "kpi"],
        "rationale": "Default executive board (dynamic composition unavailable).",
    }

KB_COLLECTION = "bounce-board-kb"
KB_NAMESPACE = "bb-kb"

SOURCE_TYPE_LABELS = {
    "regulation": "Regulations",
    "sop": "SOPs",
    "manual": "Manuals",
    "incident": "Past Incidents",
    "best_practice": "Best Practices",
    "company_doc": "Company Docs",
}
