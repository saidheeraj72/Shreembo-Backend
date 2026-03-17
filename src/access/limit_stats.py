"""Auto-split limit service part."""
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List
from uuid import UUID
import logging

from src.core.database import db
from src.models.limits import (
    UsageLimit,
    UsageLimitCreate,
    UsageLimitUpdate,
    UsageLimitCheck,
    UsageStats,
    LimitType,
    EntityType,
    DEFAULT_USER_LIMITS,
    DEFAULT_ORG_LIMITS,
)

logger = logging.getLogger(__name__)


class LimitStatsMixin:
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
