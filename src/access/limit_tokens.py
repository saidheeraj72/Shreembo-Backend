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


class LimitTokenChecksMixin:
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

