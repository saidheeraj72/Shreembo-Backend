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


class LimitCrudMixin:
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

