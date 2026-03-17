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


class LimitRateDailyMixin:
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

