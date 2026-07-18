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
    "critique",
    "report_generation",
    "complete",
]
StageStatus = Literal["pending", "running", "complete", "error"]
SessionStatus = Literal["draft", "running", "complete", "error"]
KBSourceType = Literal["regulation", "sop", "manual", "incident", "best_practice", "company_doc"]
Severity = Literal["low", "medium", "high", "critical"]
# Persona ids are dynamic (the board is composed per problem), so plain str.
AgentPersonaId = str
Sentiment = Literal["support", "concern", "question", "neutral"]
IngestStep = Literal["uploading", "parsing", "chunking", "embedding", "indexed", "error"]


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


class SessionAttachment(CamelModel):
    """An uploaded session document with its extracted text (stored on the row)."""

    id: str
    name: str
    size_bytes: int
    mime_type: str
    chars_extracted: int = 0
    # Full text is kept in the JSONB column but never serialized to the client.
    text: Optional[str] = Field(default=None, exclude=True)


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
    # Free-text specific domain (e.g. "Cold-chain pharma distribution") so the
    # board is composed for the real domain, not just the 4 canonical buckets.
    industry_label: Optional[str] = None
    industry_scores: List[IndustryScore]
    department: str
    problem_type: ProblemType
    user_role: str
    keywords: List[str]
    summary: str


class BoardExpert(CamelModel):
    id: str
    name: str
    title: str
    description: str
    expertise: List[str]
    industry_label: str


class BoardPersona(CamelModel):
    id: str
    name: str
    title: str
    focus: str


class BoardRound(CamelModel):
    round: int
    topic: str
    speakers: List[str]


class BoardPlan(CamelModel):
    """The dynamically composed board for one session."""

    expert: BoardExpert
    personas: List[BoardPersona]
    rounds: List[BoardRound]
    frameworks: List[str]
    rationale: str = ""


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
    chars_extracted: Optional[int] = None
    chunks: Optional[int] = None
    truncated: bool = False
    error: Optional[str] = None


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


class RecommendationCritique(CamelModel):
    verdict: Literal["grounded", "weak", "rejected"]
    note: str


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
    assumptions: List[str] = Field(default_factory=list)
    critique: Optional[RecommendationCritique] = None
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
    # Every figure above is an estimate; these are the stated assumptions it rests on.
    assumptions: List[str] = Field(default_factory=list)


class TokenUsage(CamelModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0


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
    token_usage: Optional[TokenUsage] = None


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
    board: Optional[BoardPlan] = None
    attachments: Optional[List[SessionAttachment]] = None
    retrieved_sources: Optional[List[RetrievedSource]] = None
    frameworks: Optional[List[dict]] = None


class AskBoardRequest(CamelModel):
    question: str
    persona_id: Optional[str] = None


class ChatAction(CamelModel):
    type: str
    summary: str
    ok: bool = True


class ChatMessage(CamelModel):
    id: str
    role: Literal["user", "assistant"]
    content_md: str
    actions: List[ChatAction] = Field(default_factory=list)
    timestamp: str


class ChatRequest(CamelModel):
    message: str


class EmailIssueImportItem(CamelModel):
    """One issue row from an email-agent mailbox scan, selected for KB import."""

    key: str
    title: str
    summary: str = ""
    severity: str = "medium"          # email scans use low/medium/high
    solved: bool = False
    solution: str = ""
    occurrences: int = 0
    first_raised: Optional[str] = None
    last_raised: Optional[str] = None


class ImportEmailIssuesRequest(CamelModel):
    industry: IndustryId
    account_email: Optional[str] = None
    issues: List[EmailIssueImportItem] = Field(min_length=1, max_length=40)


class BounceBoardStats(CamelModel):
    total_sessions: int
    running_sessions: int
    total_recommendations: int
    kb_documents: int
