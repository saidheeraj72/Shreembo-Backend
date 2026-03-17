"""Token usage tracking service."""
from typing import Optional, List
from uuid import UUID
from datetime import date, datetime
import logging

from src.core.database import db

logger = logging.getLogger(__name__)


class TokenUsageService:
    """Service for tracking and retrieving token usage."""

    @staticmethod
    async def track_usage(
        user_id: UUID,
        org_id: Optional[UUID],
        prompt_tokens: int,
        completion_tokens: int,
        is_rag: bool = False,
        is_web_search: bool = False
    ) -> None:
        """
        Track token usage for a chat request.
        Uses PostgreSQL function for atomic upsert.
        """
        try:
            await db.admin.rpc("increment_token_usage", {
                "p_user_id": str(user_id),
                "p_org_id": str(org_id) if org_id else None,
                "p_prompt_tokens": prompt_tokens,
                "p_completion_tokens": completion_tokens,
                "p_is_rag": is_rag,
                "p_is_web_search": is_web_search
            }).execute()
        except Exception as e:
            # Fallback to manual upsert if RPC fails
            await TokenUsageService._manual_track_usage(
                user_id, org_id, prompt_tokens, completion_tokens,
                is_rag, is_web_search
            )

    @staticmethod
    async def _manual_track_usage(
        user_id: UUID,
        org_id: Optional[UUID],
        prompt_tokens: int,
        completion_tokens: int,
        is_rag: bool,
        is_web_search: bool
    ) -> None:
        """Manual fallback for token tracking."""
        period_start = date.today().replace(day=1)

        # Try to get existing record
        query = db.admin.table("token_usage").select("*").eq(
            "user_id", str(user_id)
        ).eq("period_start", period_start.isoformat())

        if org_id:
            query = query.eq("org_id", str(org_id))
        else:
            query = query.is_("org_id", "null")

        try:
            result = query.maybe_single().execute()
            data = result.data
        except Exception as e:
            error_str = str(e)
            # Handle 406 Not Acceptable (PostgREST empty result) or 204 Missing Response
            if "406" in error_str or "'code': '204'" in error_str:
                data = None
            else:
                # Re-raise other errors
                logger.error("Error fetching token usage: %s", e)
                raise e

        if data:
            # Update existing
            existing = data
            db.admin.table("token_usage").update({
                "prompt_tokens": existing["prompt_tokens"] + prompt_tokens,
                "completion_tokens": existing["completion_tokens"] + completion_tokens,
                "total_tokens": existing["total_tokens"] + prompt_tokens + completion_tokens,
                "chat_requests": existing["chat_requests"] + 1,
                "rag_requests": existing["rag_requests"] + (1 if is_rag else 0),
                "web_search_requests": existing["web_search_requests"] + (1 if is_web_search else 0),
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", existing["id"]).execute()
        else:
            # Insert new
            db.admin.table("token_usage").insert({
                "user_id": str(user_id),
                "org_id": str(org_id) if org_id else None,
                "period_start": period_start.isoformat(),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "chat_requests": 1,
                "rag_requests": 1 if is_rag else 0,
                "web_search_requests": 1 if is_web_search else 0
            }).execute()

    @staticmethod
    async def get_user_usage(
        user_id: UUID,
        org_id: Optional[UUID] = None,
        period_start: Optional[date] = None,
        limit: int = 12
    ) -> List[dict]:
        """
        Get token usage for a user.

        Args:
            user_id: The user to get usage for
            org_id: If provided, get org usage; if None, get personal usage
            period_start: If provided, get specific month; otherwise get recent months
            limit: Number of periods to return
        """
        query = db.admin.table("token_usage").select("*").eq("user_id", str(user_id))

        if org_id:
            query = query.eq("org_id", str(org_id))
        else:
            query = query.is_("org_id", "null")

        if period_start:
            query = query.eq("period_start", period_start.isoformat())

        result = query.order("period_start", desc=True).limit(limit).execute()
        return result.data if result.data else []

    @staticmethod
    async def get_user_all_usage(
        user_id: UUID,
        limit: int = 12
    ) -> List[dict]:
        """Get all token usage for a user (both org and personal)."""
        result = db.admin.table("token_usage").select("*").eq(
            "user_id", str(user_id)
        ).order("period_start", desc=True).limit(limit).execute()
        return result.data if result.data else []

    @staticmethod
    async def get_org_usage(
        org_id: UUID,
        period_start: Optional[date] = None,
        limit: int = 12
    ) -> List[dict]:
        """Get aggregated token usage for an organization."""
        query = db.admin.table("token_usage").select("*").eq("org_id", str(org_id))

        if period_start:
            query = query.eq("period_start", period_start.isoformat())

        result = query.order("period_start", desc=True).limit(limit).execute()
        return result.data if result.data else []

    @staticmethod
    async def get_org_usage_summary(
        org_id: UUID,
        period_start: Optional[date] = None
    ) -> dict:
        """Get summarized org usage for a specific period or current month."""
        if not period_start:
            period_start = date.today().replace(day=1)

        result = db.admin.table("token_usage").select("*").eq(
            "org_id", str(org_id)
        ).eq("period_start", period_start.isoformat()).execute()

        data = result.data if result.data else []

        # Aggregate
        summary = {
            "period_start": period_start.isoformat(),
            "total_prompt_tokens": sum(d["prompt_tokens"] for d in data),
            "total_completion_tokens": sum(d["completion_tokens"] for d in data),
            "total_tokens": sum(d["total_tokens"] for d in data),
            "total_chat_requests": sum(d["chat_requests"] for d in data),
            "total_rag_requests": sum(d["rag_requests"] for d in data),
            "total_web_search_requests": sum(d["web_search_requests"] for d in data),
            "user_count": len(data),
            "users": data
        }
        return summary

    @staticmethod
    async def get_current_month_usage(
        user_id: UUID,
        org_id: Optional[UUID] = None
    ) -> dict:
        """Get current month's usage for quick display."""
        period_start = date.today().replace(day=1)
        usage = await TokenUsageService.get_user_usage(
            user_id, org_id, period_start, limit=1
        )

        if usage:
            return usage[0]

        # Return empty usage
        return {
            "user_id": str(user_id),
            "org_id": str(org_id) if org_id else None,
            "period_start": period_start.isoformat(),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "chat_requests": 0,
            "rag_requests": 0,
            "web_search_requests": 0
        }


token_usage_service = TokenUsageService()
