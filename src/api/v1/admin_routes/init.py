"""Admin init endpoint - returns all admin data in a single call."""
import asyncio
from uuid import UUID
from fastapi import APIRouter, Depends

from src.core.dependencies import get_current_org_context
from src.api.deps.permissions import require_permission
from src.admin.service import admin_service
from src.access.group import group_service

router = APIRouter()


@router.get(
    "/init",
    response_model=dict,
    dependencies=[Depends(require_permission("users", "view"))],
)
async def admin_init(
    org_context: dict = Depends(get_current_org_context),
):
    """
    Get all admin data in a single call.

    Returns roles, permission modules, members, branches, and groups concurrently.
    This reduces multiple sequential API calls to a single request.

    **Requires:** users.view permission
    """
    org_id = UUID(org_context["org_id"])

    # Use asyncio.to_thread to truly parallelize synchronous Supabase calls
    roles, modules, users, branches, groups = await asyncio.gather(
        asyncio.to_thread(lambda: asyncio.run(admin_service.list_roles(org_id))),
        asyncio.to_thread(lambda: asyncio.run(admin_service.get_all_permissions())),
        asyncio.to_thread(lambda: asyncio.run(admin_service.list_org_users(org_id))),
        asyncio.to_thread(lambda: asyncio.run(admin_service.list_branches(org_id, include_inactive=True))),
        asyncio.to_thread(lambda: asyncio.run(group_service.list_groups(org_id))),
    )

    return {
        "roles": roles,
        "modules": modules,
        "users": users,
        "branches": branches,
        "groups": groups,
    }
