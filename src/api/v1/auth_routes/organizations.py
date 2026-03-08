from typing import Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from uuid import UUID

from src.models.auth import LoginRequest, LoginResponse, SignupRequest, SignupResponse, AcceptInvitationRequest, VerifyInviteResponse
from src.models.user import UserWithPermissions
from src.auth.service import auth_service
from src.core.dependencies import get_current_user, get_current_org_context, get_client_ip, get_user_agent, get_current_user_optional

router = APIRouter()

@router.get("/organizations")
async def get_user_organizations(
    user: dict = Depends(get_current_user),
):
    """
    Get all organizations the current user is a member of.

    Returns list of organizations with membership details including:
    - Organization id, name, slug, logo
    - User's role in each organization
    - Join date
    """
    user_id = UUID(user["id"])
    organizations = await auth_service.get_user_organizations(user_id)

    return {
        "organizations": organizations,
        "count": len(organizations),
    }


class SwitchOrganizationRequest(BaseModel):
    org_id: Optional[str] = None  # None means switch to personal workspace


@router.post("/switch-organization")
async def switch_organization(
    data: SwitchOrganizationRequest,
    user: dict = Depends(get_current_user),
):
    """
    Switch user's active organization context.

    - **org_id**: Target organization ID (null/empty for personal workspace)

    Returns updated user profile with new permissions.
    """
    user_id = UUID(user["id"])
    target_org_id = UUID(data.org_id) if data.org_id else None

    updated_user = await auth_service.switch_organization(user_id, target_org_id)

    return {
        "success": True,
        "user": updated_user,
        "message": f"Switched to {'personal workspace' if not target_org_id else 'organization'} successfully",
    }


class CreateOrganizationRequest(BaseModel):
    name: str
    slug: Optional[str] = None
    domain: Optional[str] = None


@router.post("/create-organization")
async def create_organization(
    data: CreateOrganizationRequest,
    user: dict = Depends(get_current_user),
):
    """
    Create a new organization and become its owner.

    - **name**: Organization name
    - **slug**: URL-friendly identifier (auto-generated if not provided)
    - **domain**: Organization domain (optional)

    Returns the created organization and switches user to it.
    """
    user_id = UUID(user["id"])
    
    result = await auth_service.create_organization(
        user_id=user_id,
        name=data.name,
        slug=data.slug,
        domain=data.domain,
    )

    return {
        "success": True,
        "organization": result["organization"],
        "user": result["user"],
        "message": f"Organization '{data.name}' created successfully",
    }

