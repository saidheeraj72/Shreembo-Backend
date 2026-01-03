"""Models for usage limits and tracking."""
from datetime import datetime, date
from typing import Optional, Literal
from pydantic import BaseModel, Field
from uuid import UUID


# Limit Types
EntityType = Literal["user", "organization"]
LimitType = Literal["monthly_tokens", "daily_rag_requests", "daily_chat_requests", "requests_per_minute"]
Period = Literal["daily", "monthly", "minute"]


class UsageLimit(BaseModel):
    """Usage limit configuration."""
    id: UUID
    entity_type: EntityType
    entity_id: UUID
    limit_type: LimitType
    limit_value: int
    period: Period
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UsageLimitCreate(BaseModel):
    """Create usage limit."""
    entity_type: EntityType
    entity_id: UUID
    limit_type: LimitType
    limit_value: int
    period: Period


class UsageLimitUpdate(BaseModel):
    """Update usage limit."""
    limit_value: Optional[int] = None


class UsageRateTracking(BaseModel):
    """Rate tracking for requests per minute."""
    id: UUID
    user_id: UUID
    org_id: Optional[UUID]
    window_start: datetime
    request_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UsageDailySummary(BaseModel):
    """Daily usage summary."""
    id: UUID
    user_id: UUID
    org_id: Optional[UUID]
    usage_date: date
    chat_requests: int
    rag_requests: int
    web_search_requests: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UsageLimitCheck(BaseModel):
    """Result of usage limit check."""
    allowed: bool
    limit_type: LimitType
    current_usage: int
    limit_value: int
    remaining: int
    reset_at: Optional[datetime] = None
    message: Optional[str] = None


class UsageStats(BaseModel):
    """Usage statistics for display."""
    entity_type: EntityType
    entity_id: UUID

    # Token usage
    monthly_tokens_used: int = 0
    monthly_tokens_limit: int = 0
    monthly_tokens_remaining: int = 0
    monthly_reset_date: Optional[datetime] = None

    # Daily requests
    daily_chat_requests_used: int = 0
    daily_chat_requests_limit: int = 0
    daily_chat_requests_remaining: int = 0

    daily_rag_requests_used: int = 0
    daily_rag_requests_limit: int = 0
    daily_rag_requests_remaining: int = 0

    # Rate limiting
    current_requests_per_minute: int = 0
    requests_per_minute_limit: int = 0
    requests_per_minute_remaining: int = 0


# Default limits
DEFAULT_USER_LIMITS = {
    "monthly_tokens": 100_000,
    "daily_rag_requests": 50,
    "daily_chat_requests": 100,
    "requests_per_minute": 10
}

DEFAULT_ORG_LIMITS = {
    "monthly_tokens": 1_000_000,
    "daily_rag_requests": 500,
    "daily_chat_requests": 1000,
    "requests_per_minute": 50
}
