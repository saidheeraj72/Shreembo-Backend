from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel

from src.core.dependencies import get_current_user, get_current_org_context
from src.api.deps.permissions import require_permission
from src.admin.service import admin_service
from src.audit.service import audit_service
from src.access.group import group_service
from src.access.permission import permission_service
from src.access.limit import limit_service
from src.models.admin import (
    Branch,
    BranchCreate,
    BranchUpdate,
    BranchWithManager,
    BranchUserAssignment,
    OrganizationMemberResponse,
    MemberUpdate,
    RoleChangeRequest,
    UserWithRole,
    UserPermissionGrant,
    InvitationCreate,
    InvitationResponse,
    RoleCreate,
    RoleUpdate,
    RoleResponse,
    RoleWithPermissions,
)
from src.models.audit import (
    AuditLogFilters,
    AuditAction,
    LogLevel,
    AuditLog,
)
from src.models.group import (
    Group,
    GroupCreate,
    GroupUpdate,
    GroupMemberAdd,
    GroupMemberRemove,
)

router = APIRouter()

# ==========================================
# AUDIT LOG ENDPOINTS
# ==========================================

@router.get(
    "/audit-logs",
    response_model=dict,
    dependencies=[Depends(require_permission("audit", "view"))],
)
async def list_audit_logs(
    action: Optional[AuditAction] = None,
    resource_type: Optional[str] = None,
    user_id: Optional[UUID] = None,
    severity: Optional[LogLevel] = None,
    page: int = 1,
    limit: int = 50,
    org_context: dict = Depends(get_current_org_context),
):
    """
    List audit logs for the organization.

    **Requires:** audit.view permission

    Query Parameters:
    - **action**: Filter by action type
    - **resource_type**: Filter by resource type
    - **user_id**: Filter by user
    - **severity**: Filter by severity
    - **page**: Page number (default: 1)
    - **limit**: Items per page (default: 50)
    """
    org_id = UUID(org_context["org_id"])
    
    # Create filters object
    filters = AuditLogFilters(
        action=action,
        resource_type=resource_type,
        user_id=user_id,
        severity=severity,
        page=page,
        limit=limit,
    )
    
    logs, total = await audit_service.get_logs(org_id, filters)

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "logs": logs,
    }
