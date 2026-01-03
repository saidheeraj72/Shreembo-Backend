"""API endpoints for usage limits management."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from uuid import UUID

from src.core.dependencies import get_current_user
from src.api.deps.permissions import require_permission
from src.models.user import UserWithPermissions
from src.models.limits import (
    UsageLimit,
    UsageLimitCreate,
    UsageLimitUpdate,
    UsageStats,
    EntityType,
    LimitType
)
from src.services.limit_service import limit_service


router = APIRouter(prefix="/limits", tags=["limits"])


@router.get("/me", response_model=UsageStats)
async def get_my_usage_stats(
    current_user: UserWithPermissions = Depends(get_current_user)
):
    """Get current user's usage statistics."""
    stats = await limit_service.get_usage_stats(
        entity_type="user",
        entity_id=current_user.id
    )
    return stats


@router.get("/organization/{org_id}", response_model=UsageStats)
async def get_organization_usage_stats(
    org_id: UUID,
    current_user: UserWithPermissions = Depends(
        require_permission("usage", "view")
    )
):
    """Get organization usage statistics (admin only)."""
    stats = await limit_service.get_usage_stats(
        entity_type="organization",
        entity_id=org_id
    )
    return stats


@router.get("/{entity_type}/{entity_id}", response_model=List[UsageLimit])
async def get_entity_limits(
    entity_type: EntityType,
    entity_id: UUID,
    current_user: UserWithPermissions = Depends(
        require_permission("limits", "view")
    )
):
    """Get all limits for an entity (admin only)."""
    limits = await limit_service.get_all_limits(entity_type, entity_id)
    return limits


@router.post("/{entity_type}/{entity_id}", response_model=UsageLimit)
async def create_usage_limit(
    entity_type: EntityType,
    entity_id: UUID,
    limit_data: UsageLimitCreate,
    current_user: UserWithPermissions = Depends(
        require_permission("limits", "manage")
    )
):
    """Create a new usage limit (admin only)."""
    # Ensure entity_type and entity_id match the payload
    if limit_data.entity_type != entity_type or limit_data.entity_id != entity_id:
        raise HTTPException(
            status_code=400,
            detail="Entity type and ID must match URL parameters"
        )

    try:
        limit = await limit_service.create_limit(limit_data)
        return limit
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/{entity_type}/{entity_id}/{limit_type}",
    response_model=UsageLimit
)
async def update_usage_limit(
    entity_type: EntityType,
    entity_id: UUID,
    limit_type: LimitType,
    update_data: UsageLimitUpdate,
    current_user: UserWithPermissions = Depends(
        require_permission("limits", "manage")
    )
):
    """Update an existing usage limit (admin only)."""
    try:
        limit = await limit_service.update_limit(
            entity_type, entity_id, limit_type, update_data
        )
        return limit
    except Exception as e:
        raise HTTPException(status_code=404, detail="Limit not found")


@router.post("/{entity_type}/{entity_id}/defaults", response_model=List[UsageLimit])
async def create_default_limits(
    entity_type: EntityType,
    entity_id: UUID,
    current_user: UserWithPermissions = Depends(
        require_permission("limits", "manage")
    )
):
    """Create default limits for an entity (admin only)."""
    limits = await limit_service.create_default_limits(entity_type, entity_id)
    return limits
