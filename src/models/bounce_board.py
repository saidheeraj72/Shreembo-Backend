"""
Bounce Board — Pydantic schemas.

These mirror the frontend contract in
enterprise-intelligence-hub/src/types/bounce-board.ts exactly. All models
serialize to camelCase (alias generator) so the JSON matches the TS types
with zero frontend changes; populate_by_name lets backend code build them
from snake_case too.
"""
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


IndustryId = Literal["shipping", "healthcare", "manufacturing", "logistics"]
ProblemType = Literal["issue", "idea", "question", "decision"]
PipelineStageId = Literal[
    "intake",
    "context_detection",
    "agent_routing",
    "knowledge_retrieval",
    "framework_analysis",
    "board_discussion",
    "decision_ranking",
    "report_generation",
    "complete",
]
StageStatus = Literal["pending", "running", "complete", "error"]
SessionStatus = Literal["draft", "running", "complete", "error"]
KBSourceType = Literal["regulation", "sop", "manual", "incident", "best_practice", "company_doc"]
Severity = Literal["low", "medium", "high", "critical"]
AgentPersonaId = Literal["ceo", "cfo", "coo", "cto", "industry_expert", "risk_expert"]
Sentiment = Literal["support", "concern", "question", "neutral"]
IngestStep = Literal["uploading", "parsing", "chunking", "embedding", "indexed"]


class StageState(CamelModel):
    id: PipelineStageId
    status: StageStatus
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    detail: Optional[str] = None


class AttachmentMeta(CamelModel):
    id: str
    name: str
    size_bytes: int
    mime_type: str


class KpiInput(CamelModel):
    name: str
    current: float
    target: float
    unit: str


class SessionInput(CamelModel):
    title: str
    problem_statement: str
    auto_detect: bool = True
    industry_hint: Optional[IndustryId] = None
    department: Optional[str] = None
    problem_type: Optional[ProblemType] = None
    user_role: Optional[str] = None
    attachments: List[AttachmentMeta] = Field(default_factory=list)
    kpis: List[KpiInput] = Field(default_factory=list)


class IndustryScore(CamelModel):
    industry: IndustryId
    confidence: float


class ContextDetection(CamelModel):
    industry: IndustryId
    industry_scores: List[IndustryScore]
    department: str
    problem_type: ProblemType
    user_role: str
    keywords: List[str]
    summary: str


class IndustryAgent(CamelModel):
    id: str
    industry: IndustryId
    name: str
    description: str
    expertise: List[str]
    kb_source_count: int


class RetrievedSource(CamelModel):
    kb_issue_id: str
    title: str
    source_type: KBSourceType
    relevance: float
    excerpt: str


class KBIssue(CamelModel):
    id: str
    title: str
    industry: IndustryId
    source_type: KBSourceType
    severity: Severity
    tags: List[str]
    summary: str
    content_md: str
    related_issue_ids: List[str]
    added_at: str
    times_referenced: int


class KBMindMapNode(CamelModel):
    id: str
    label: str
    type: Literal["industry", "category", "issue", "source"]
    severity: Optional[Severity] = None
    kb_issue_id: Optional[str] = None


class KBMindMapEdge(CamelModel):
    id: str
    source: str
    target: str


class KBMindMap(CamelModel):
    nodes: List[KBMindMapNode]
    edges: List[KBMindMapEdge]


class IngestRequest(CamelModel):
    file_name: str
    size_bytes: int
    source_type: KBSourceType
    industry: IndustryId
    description: Optional[str] = None


class IngestJob(CamelModel):
    job_id: str
    file_name: str
    step: IngestStep
    progress: int
    issues_created: Optional[int] = None


class FiveWhysOutput(CamelModel):
    framework: Literal["five_whys"] = "five_whys"
    problem: str
    whys: List[dict]
    root_cause: str


class RootCauseOutput(CamelModel):
    framework: Literal["root_cause"] = "root_cause"
    categories: List[dict]
    primary_cause: str


class SwotOutput(CamelModel):
    framework: Literal["swot"] = "swot"
    strengths: List[str]
    weaknesses: List[str]
    opportunities: List[str]
    threats: List[str]


class GapOutput(CamelModel):
    framework: Literal["gap"] = "gap"
    rows: List[dict]


class RiskOutput(CamelModel):
    framework: Literal["risk"] = "risk"
    risks: List[dict]


class KpiOutput(CamelModel):
    framework: Literal["kpi"] = "kpi"
    metrics: List[dict]


FrameworkOutput = Union[
    FiveWhysOutput, RootCauseOutput, SwotOutput, GapOutput, RiskOutput, KpiOutput
]


class DiscussionMessage(CamelModel):
    id: str
    agent_id: AgentPersonaId
    round: int
    round_topic: str
    content_md: str
    sentiment: Sentiment
    citations: List[RetrievedSource] = Field(default_factory=list)
    timestamp: str


class DiscussionState(CamelModel):
    status: Literal["idle", "streaming", "complete"]
    active_agent_id: Optional[AgentPersonaId] = None
    messages: List[DiscussionMessage] = Field(default_factory=list)


class RecommendationScores(CamelModel):
    impact: int
    cost: int
    risk: int
    urgency: int
    feasibility: int


class Recommendation(CamelModel):
    id: str
    rank: int
    title: str
    description: str
    category: str
    scores: RecommendationScores
    composite_score: int
    estimated_cost: float
    estimated_roi_pct: float
    effort_weeks: int
    supporting_agents: List[AgentPersonaId] = Field(default_factory=list)
    dissenting_agents: List[AgentPersonaId] = Field(default_factory=list)
    source_refs: List[RetrievedSource] = Field(default_factory=list)


class ActionItem(CamelModel):
    id: str
    title: str
    owner_role: str
    phase: str
    start_week: int
    duration_weeks: int
    depends_on: List[str] = Field(default_factory=list)


class ReportPhase(CamelModel):
    name: str
    start_week: int
    end_week: int


class CostBreakdownItem(CamelModel):
    label: str
    amount: float


class CostRoi(CamelModel):
    total_cost: float
    expected_roi_pct: float
    payback_months: int
    breakdown: List[CostBreakdownItem]


class RiskRegisterEntry(CamelModel):
    id: str
    title: str
    severity: Severity
    owner: str
    mitigation: str
    status: Literal["open", "mitigating", "closed"]


class Report(CamelModel):
    session_id: str
    generated_at: str
    executive_summary_md: str
    recommendations: List[Recommendation]
    action_plan: List[ActionItem]
    phases: List[ReportPhase]
    cost_roi: CostRoi
    risk_register: List[RiskRegisterEntry]
    management_report_md: str


class SessionSummary(CamelModel):
    id: str
    title: str
    problem_excerpt: str
    status: SessionStatus
    industry: Optional[IndustryId] = None
    current_stage: PipelineStageId
    stages_complete: int
    created_at: str
    updated_at: str


class Session(SessionSummary):
    input: SessionInput
    stages: List[StageState]
    context: Optional[ContextDetection] = None
    routed_agent_id: Optional[str] = None
    retrieved_sources: Optional[List[RetrievedSource]] = None
    frameworks: Optional[List[dict]] = None


class BounceBoardStats(CamelModel):
    total_sessions: int
    running_sessions: int
    total_recommendations: int
    kb_documents: int
