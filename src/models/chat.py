"""Chat and RAG chatbot Pydantic models."""
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime, date
from enum import Enum
from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """Chat message roles."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class SessionStatus(str, Enum):
    """Chat session status."""
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class EmbeddingStatus(str, Enum):
    """Document embedding status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# ============== Request Models ==============

class ChatSessionCreate(BaseModel):
    """Create a new chat session."""
    title: Optional[str] = "New Chat"
    rag_enabled: bool = True
    web_search_enabled: bool = False


class ChatSessionUpdate(BaseModel):
    """Update chat session settings."""
    title: Optional[str] = None
    rag_enabled: Optional[bool] = None
    web_search_enabled: Optional[bool] = None
    is_shared: Optional[bool] = None


class ChatMessageRequest(BaseModel):
    """Send a message to the chatbot."""
    content: str = Field(..., min_length=1, max_length=32000)
    rag_enabled: Optional[bool] = None  # Override session default
    web_search_enabled: Optional[bool] = None  # Override session default


class SessionDocumentUploadInit(BaseModel):
    """Initialize document upload within chat session."""
    filename: str
    content_type: str
    size_bytes: int


class SessionDocumentUploadComplete(BaseModel):
    """Complete document upload within chat session."""
    s3_key: str
    filename: str
    file_type: str
    file_size: int
    mime_type: str


# ============== Response Models ==============

class RAGSource(BaseModel):
    """Source document reference for RAG responses."""
    document_id: str
    document_name: str
    chunk_text: str
    chunk_index: int
    score: float


class WebSearchResult(BaseModel):
    """Web search result."""
    title: str
    url: str
    snippet: str


class MessageAttachment(BaseModel):
    """Document attachment in a message."""
    filename: str
    file_type: Optional[str] = None
    file_size: int
    status: Optional[str] = None
    session_document_id: Optional[UUID] = None  # Set after processing completes


class ChatMessageResponse(BaseModel):
    """Chat message response."""
    id: UUID
    session_id: UUID
    role: MessageRole
    content: str
    attachments: Optional[List[MessageAttachment]] = None
    sources: Optional[List[RAGSource]] = None
    web_search_results: Optional[List[WebSearchResult]] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ChatSessionResponse(BaseModel):
    """Chat session response."""
    id: UUID
    org_id: Optional[UUID] = None
    user_id: UUID
    title: str
    rag_enabled: bool
    web_search_enabled: bool
    is_shared: bool
    status: SessionStatus
    message_count: int
    last_message_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChatSessionWithMessages(ChatSessionResponse):
    """Chat session with messages."""
    messages: List[ChatMessageResponse] = []


class SessionDocumentResponse(BaseModel):
    """Session document response."""
    id: UUID
    session_id: UUID
    filename: str
    file_type: Optional[str] = None
    file_size: int
    embedding_status: EmbeddingStatus
    uploaded_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SessionDocumentUploadResponse(BaseModel):
    """Response for session document upload init."""
    upload_id: str
    upload_url: str
    s3_key: str
    session_id: str
    user_id: str
    filename: str
    file_type: Optional[str] = None
    file_size: int
    mime_type: str


# ============== Token Usage Models ==============

class TokenUsageResponse(BaseModel):
    """Token usage for a period."""
    id: UUID
    user_id: UUID
    org_id: Optional[UUID] = None
    period_start: date
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    chat_requests: int
    rag_requests: int
    web_search_requests: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TokenUsageSummary(BaseModel):
    """Aggregated token usage summary."""
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_chat_requests: int
    total_rag_requests: int
    total_web_search_requests: int
    periods: List[TokenUsageResponse] = []


# ============== WebSocket Message Types ==============

class WSMessageType(str, Enum):
    """WebSocket message types."""
    # Outgoing (server -> client)
    STREAM_START = "stream_start"
    STREAM_CHUNK = "stream_chunk"
    STREAM_END = "stream_end"
    STREAM_ERROR = "stream_error"
    RAG_CONTEXT = "rag_context"
    WEB_SEARCH = "web_search"
    TYPING = "typing"

    # Incoming (client -> server)
    SEND_MESSAGE = "send_message"
    STOP_GENERATION = "stop_generation"
    JOIN_SESSION = "join_session"
    LEAVE_SESSION = "leave_session"


class WSIncomingMessage(BaseModel):
    """WebSocket incoming message from client."""
    type: WSMessageType
    session_id: UUID
    content: Optional[str] = None
    rag_enabled: Optional[bool] = None
    web_search_enabled: Optional[bool] = None


class WSOutgoingMessage(BaseModel):
    """WebSocket outgoing message to client."""
    type: WSMessageType
    session_id: UUID
    message_id: Optional[UUID] = None
    content: Optional[str] = None
    sources: Optional[List[RAGSource]] = None
    web_search_results: Optional[List[WebSearchResult]] = None
    error: Optional[str] = None
    token_usage: Optional[Dict[str, int]] = None
