"""
Pydantic models for the Email Agent module.
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class ConnectStartResponse(BaseModel):
    """Returned by the OAuth start endpoint."""
    authorization_url: str


class EmailAccountResponse(BaseModel):
    """A connected mailbox (no token material exposed)."""
    id: UUID
    provider: str
    email_address: str
    status: str
    created_at: datetime
    updated_at: datetime


class EmailListItem(BaseModel):
    """A single email summary in a list/search result."""
    id: str
    thread_id: Optional[str] = None
    subject: Optional[str] = None
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    date: Optional[str] = None
    snippet: str = ""
    label_ids: List[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class EmailDetail(EmailListItem):
    """Full email including body."""
    cc: Optional[str] = None
    body: str = ""


class EmailListResponse(BaseModel):
    account_id: UUID
    items: List[EmailListItem]
    next_page_token: Optional[str] = None


class SendEmailRequest(BaseModel):
    """Request body for sending an email."""
    to: EmailStr
    subject: str = Field(..., max_length=998)
    body: str = Field(..., min_length=1)
    cc: Optional[str] = None
    bcc: Optional[str] = None
    # For replies: the RFC822 Message-Id being replied to + Gmail thread id.
    in_reply_to: Optional[str] = None
    thread_id: Optional[str] = None


class SendEmailResponse(BaseModel):
    id: str
    thread_id: Optional[str] = None
    status: str = "sent"


class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str


class EmailAgentChatRequest(BaseModel):
    """Conversation history for the email assistant; last item is the new user turn."""
    messages: List[ChatMessage] = Field(..., min_length=1)


class EmailAgentChatResponse(BaseModel):
    reply: str
    actions: List[str] = Field(default_factory=list)


class MindMapRequest(BaseModel):
    """Trigger a scan over the mailbox to build a mind map."""
    query: Optional[str] = Field(
        default=None,
        description="Optional Gmail query; defaults to recent inbox.",
    )
    max_emails: int = Field(default=200, ge=1, le=200)
    map_type: str = Field(
        default="problems",
        description="Which mind map to build: problems, actions, topics, people.",
    )


class MindMapNode(BaseModel):
    id: str
    label: str
    type: str
    summary: str = ""
    source_email_ids: List[str] = Field(default_factory=list)


class MindMapEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: str


class MindMapResponse(BaseModel):
    account_id: UUID
    nodes: List[MindMapNode] = Field(default_factory=list)
    edges: List[MindMapEdge] = Field(default_factory=list)
    email_count: int = 0
    generated_at: str
