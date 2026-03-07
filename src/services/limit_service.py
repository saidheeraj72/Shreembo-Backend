"""Service for managing and enforcing usage limits."""
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List
from uuid import UUID
import math
import logging

from src.core.database import db

logger = logging.getLogger(__name__)
from src.models.limits import (
    UsageLimit,
    UsageLimitCreate,
    UsageLimitUpdate,
    UsageLimitCheck,
    UsageStats,
    LimitType,
    EntityType,
    DEFAULT_USER_LIMITS,
    DEFAULT_ORG_LIMITS
)


class LimitService:
    """Service for usage limit management and enforcement."""

    @staticmethod
    async def get_limit(
        entity_type: EntityType,
        entity_id: UUID,
        limit_type: LimitType
    ) -> Optional[UsageLimit]:
        """Get a specific usage limit."""
        result = db.admin.table("usage_limits").select("*").eq(
            "entity_type", entity_type
        ).eq("entity_id", str(entity_id)).eq("limit_type", limit_type).execute()

        if result.data:
            return UsageLimit(**result.data[0])
        return None

    @staticmethod
    async def get_all_limits(
        entity_type: EntityType,
        entity_id: UUID
    ) -> List[UsageLimit]:
        """Get all limits for an entity."""
        result = db.admin.table("usage_limits").select("*").eq(
            "entity_type", entity_type
        ).eq("entity_id", str(entity_id)).execute()

        return [UsageLimit(**limit) for limit in result.data]

    @staticmethod
    async def create_limit(limit_data: UsageLimitCreate) -> UsageLimit:
        """Create a new usage limit."""
        result = db.admin.table("usage_limits").insert({
            "entity_type": limit_data.entity_type,
            "entity_id": str(limit_data.entity_id),
            "limit_type": limit_data.limit_type,
            "limit_value": limit_data.limit_value,
            "period": limit_data.period
        }).execute()

        return UsageLimit(**result.data[0])

    @staticmethod
    async def update_limit(
        entity_type: EntityType,
        entity_id: UUID,
        limit_type: LimitType,
        update_data: UsageLimitUpdate
    ) -> UsageLimit:
        """Update an existing usage limit."""
        update_dict = update_data.model_dump(exclude_unset=True)

        result = db.admin.table("usage_limits").update(update_dict).eq(
            "entity_type", entity_type
        ).eq("entity_id", str(entity_id)).eq("limit_type", limit_type).execute()

        return UsageLimit(**result.data[0])

    @staticmethod
    async def create_default_limits(
        entity_type: EntityType,
        entity_id: UUID
    ) -> List[UsageLimit]:
        """Create default limits for a new user or organization."""
        defaults = DEFAULT_USER_LIMITS if entity_type == "user" else DEFAULT_ORG_LIMITS
        limits = []

        limit_configs = [
            ("monthly_tokens", "monthly", defaults["monthly_tokens"]),
            ("daily_rag_requests", "daily", defaults["daily_rag_requests"]),
            ("daily_chat_requests", "daily", defaults["daily_chat_requests"]),
            ("requests_per_minute", "minute", defaults["requests_per_minute"])
        ]

        for limit_type, period, limit_value in limit_configs:
            try:
                limit = await LimitService.create_limit(UsageLimitCreate(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    limit_type=limit_type,  # type: ignore
                    limit_value=limit_value,
                    period=period  # type: ignore
                ))
                limits.append(limit)
            except Exception as e:
                # Limit already exists, skip
                logger.debug("Limit already exists for %s:%s:%s: %s", entity_type, entity_id, limit_type, e)

        return limits

    @staticmethod
    async def check_rate_limit(
        user_id: UUID,
        org_id: Optional[UUID]
    ) -> UsageLimitCheck:
        """Check if user is within rate limit (requests per minute)."""
        # Get limit
        entity_type = "organization" if org_id else "user"
        entity_id = org_id if org_id else user_id

        limit = await LimitService.get_limit(entity_type, entity_id, "requests_per_minute")
        if not limit:
            # No limit set, allow
            return UsageLimitCheck(
                allowed=True,
                limit_type="requests_per_minute",
                current_usage=0,
                limit_value=999999,
                remaining=999999
            )

        # Get current minute window
        now = datetime.utcnow()
        window_start = now.replace(second=0, microsecond=0)

        # Increment and get count using database function
        result = db.admin.rpc("increment_rate_tracking", {
            "p_user_id": str(user_id),
            "p_org_id": str(org_id) if org_id else None,
            "p_window_start": window_start.isoformat()
        }).execute()

        current_count = result.data if result.data else 1

        # Check if over limit
        allowed = current_count <= limit.limit_value
        remaining = max(0, limit.limit_value - current_count)

        return UsageLimitCheck(
            allowed=allowed,
            limit_type="requests_per_minute",
            current_usage=current_count,
            limit_value=limit.limit_value,
            remaining=remaining,
            reset_at=window_start + timedelta(minutes=1),
            message=f"Rate limit: {current_count}/{limit.limit_value} requests this minute" if not allowed else None
        )

    @staticmethod
    async def check_daily_limit(
        user_id: UUID,
        org_id: Optional[UUID],
        limit_type: LimitType
    ) -> UsageLimitCheck:
        """Check if user is within daily limit."""
        # Get limit
        entity_type = "organization" if org_id else "user"
        entity_id = org_id if org_id else user_id

        limit = await LimitService.get_limit(entity_type, entity_id, limit_type)
        if not limit:
            # No limit set, allow
            return UsageLimitCheck(
                allowed=True,
                limit_type=limit_type,
                current_usage=0,
                limit_value=999999,
                remaining=999999
            )

        # Get today's usage
        today = date.today()
        query = db.admin.table("usage_daily_summary").select("*").eq(
            "user_id", str(user_id)
        )
        
        if org_id:
            query = query.eq("org_id", str(org_id))
        else:
            query = query.is_("org_id", "null")
            
        result = query.eq("usage_date", today.isoformat()).execute()

        current_usage = 0
        if result.data:
            summary = result.data[0]
            if limit_type == "daily_chat_requests":
                current_usage = summary["chat_requests"]
            elif limit_type == "daily_rag_requests":
                current_usage = summary["rag_requests"]

        # Check if over limit
        allowed = current_usage < limit.limit_value
        remaining = max(0, limit.limit_value - current_usage)

        # Calculate reset time (midnight UTC)
        tomorrow = today + timedelta(days=1)
        reset_at = datetime.combine(tomorrow, datetime.min.time())

        return UsageLimitCheck(
            allowed=allowed,
            limit_type=limit_type,
            current_usage=current_usage,
            limit_value=limit.limit_value,
            remaining=remaining,
            reset_at=reset_at,
            message=f"Daily limit reached: {current_usage}/{limit.limit_value} requests today" if not allowed else None
        )

    @staticmethod
    async def check_monthly_token_limit(
        user_id: UUID,
        org_id: Optional[UUID]
    ) -> UsageLimitCheck:
        """Check if user is within monthly token limit."""
        # Get limit
        entity_type = "organization" if org_id else "user"
        entity_id = org_id if org_id else user_id

        limit = await LimitService.get_limit(entity_type, entity_id, "monthly_tokens")
        if not limit:
            # No limit set, allow
            return UsageLimitCheck(
                allowed=True,
                limit_type="monthly_tokens",
                current_usage=0,
                limit_value=999999999,
                remaining=999999999
            )

        # Get current month's usage from token_usage table
        today = date.today()
        period_start = date(today.year, today.month, 1)

        query = db.admin.table("token_usage").select("total_tokens").eq(
            "user_id", str(user_id)
        )

        if org_id:
            query = query.eq("org_id", str(org_id))
        else:
            query = query.is_("org_id", "null")

        result = query.eq("period_start", period_start.isoformat()).execute()

        current_usage = 0
        if result.data:
            current_usage = result.data[0]["total_tokens"]

        # Check if over limit
        allowed = current_usage < limit.limit_value
        remaining = max(0, limit.limit_value - current_usage)

        # Calculate reset time (first day of next month)
        if today.month == 12:
            next_month = date(today.year + 1, 1, 1)
        else:
            next_month = date(today.year, today.month + 1, 1)
        reset_at = datetime.combine(next_month, datetime.min.time())

        return UsageLimitCheck(
            allowed=allowed,
            limit_type="monthly_tokens",
            current_usage=current_usage,
            limit_value=limit.limit_value,
            remaining=remaining,
            reset_at=reset_at,
            message=f"Monthly token limit reached: {current_usage}/{limit.limit_value} tokens this month" if not allowed else None
        )

    @staticmethod
    async def track_request(
        user_id: UUID,
        org_id: Optional[UUID],
        is_chat: bool = True,
        is_rag: bool = False,
        is_web_search: bool = False
    ):
        """Track a request in daily usage."""
        db.admin.rpc("increment_daily_usage", {
            "p_user_id": str(user_id),
            "p_org_id": str(org_id) if org_id else None,
            "p_chat": is_chat,
            "p_rag": is_rag,
            "p_web": is_web_search
        }).execute()

    @staticmethod
    async def check_all_limits(
        user_id: UUID,
        org_id: Optional[UUID],
        is_rag: bool = False
    ) -> Dict[str, UsageLimitCheck]:
        """Check all applicable limits before processing a request."""
        checks = {}

        # Check rate limit
        checks["rate"] = await LimitService.check_rate_limit(user_id, org_id)

        # Check daily chat limit
        checks["daily_chat"] = await LimitService.check_daily_limit(
            user_id, org_id, "daily_chat_requests"
        )

        # Check daily RAG limit if RAG is enabled
        if is_rag:
            checks["daily_rag"] = await LimitService.check_daily_limit(
                user_id, org_id, "daily_rag_requests"
            )

        # Check monthly token limit
        checks["monthly_tokens"] = await LimitService.check_monthly_token_limit(
            user_id, org_id
        )

        return checks

    @staticmethod
    async def get_usage_stats(
        entity_type: EntityType,
        entity_id: UUID
    ) -> UsageStats:
        """Get comprehensive usage statistics for an entity."""
        # Get all limits
        limits = await LimitService.get_all_limits(entity_type, entity_id)
        limits_dict = {limit.limit_type: limit for limit in limits}

        stats = UsageStats(
            entity_type=entity_type,
            entity_id=entity_id
        )

        # Helper to build base query
        def build_query(table, select_cols):
            q = db.admin.table(table).select(select_cols)
            if entity_type == "user":
                # Personal usage: user_id = entity_id AND org_id IS NULL
                q = q.eq("user_id", str(entity_id)).is_("org_id", "null")
            else:
                # Org usage: org_id = entity_id (aggregates all users)
                q = q.eq("org_id", str(entity_id))
            return q

        # Monthly tokens
        if "monthly_tokens" in limits_dict:
            limit = limits_dict["monthly_tokens"]
            stats.monthly_tokens_limit = limit.limit_value

            # Get usage
            today = date.today()
            period_start = date(today.year, today.month, 1)
            
            result = build_query("token_usage", "total_tokens").eq(
                "period_start", period_start.isoformat()
            ).execute()

            if result.data:
                stats.monthly_tokens_used = sum(item["total_tokens"] for item in result.data)
            stats.monthly_tokens_remaining = max(0, stats.monthly_tokens_limit - stats.monthly_tokens_used)

            # Reset date
            if today.month == 12:
                stats.monthly_reset_date = datetime(today.year + 1, 1, 1)
            else:
                stats.monthly_reset_date = datetime(today.year, today.month + 1, 1)

        # Daily requests
        today_date = date.today()
        daily_result = build_query("usage_daily_summary", "*").eq(
            "usage_date", today_date.isoformat()
        ).execute()

        if daily_result.data:
            # Sum up usage (handles both single user and org aggregation)
            chat_used = sum(item["chat_requests"] for item in daily_result.data)
            rag_used = sum(item["rag_requests"] for item in daily_result.data)

            # Chat requests
            if "daily_chat_requests" in limits_dict:
                limit = limits_dict["daily_chat_requests"]
                stats.daily_chat_requests_limit = limit.limit_value
                stats.daily_chat_requests_used = chat_used
                stats.daily_chat_requests_remaining = max(0, stats.daily_chat_requests_limit - stats.daily_chat_requests_used)

            # RAG requests
            if "daily_rag_requests" in limits_dict:
                limit = limits_dict["daily_rag_requests"]
                stats.daily_rag_requests_limit = limit.limit_value
                stats.daily_rag_requests_used = rag_used
                stats.daily_rag_requests_remaining = max(0, stats.daily_rag_requests_limit - stats.daily_rag_requests_used)

        # Rate limit (current minute)
        if "requests_per_minute" in limits_dict:
            limit = limits_dict["requests_per_minute"]
            stats.requests_per_minute_limit = limit.limit_value

            now = datetime.utcnow()
            window_start = now.replace(second=0, microsecond=0)

            rate_result = build_query("usage_rate_tracking", "request_count").eq(
                "window_start", window_start.isoformat()
            ).execute()

            if rate_result.data:
                stats.current_requests_per_minute = sum(item["request_count"] for item in rate_result.data)
            stats.requests_per_minute_remaining = max(0, stats.requests_per_minute_limit - stats.current_requests_per_minute)

        return stats


limit_service = LimitService()
