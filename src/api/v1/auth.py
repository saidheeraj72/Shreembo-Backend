"""
Authentication API endpoints.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from uuid import UUID

from src.models.auth import LoginRequest, LoginResponse, SignupRequest, SignupResponse, AcceptInvitationRequest, VerifyInviteResponse
from src.models.user import UserWithPermissions
from src.services.auth_service import auth_service
from src.core.dependencies import get_current_user, get_current_org_context, get_client_ip, get_user_agent, get_current_user_optional

router = APIRouter()


@router.post("/signup", response_model=SignupResponse)
async def signup(
    request: Request,
    data: SignupRequest,
):
    """
    Register a new user account.

    - **email**: User email address
    - **password**: Password (minimum 8 characters)
    - **full_name**: User's full name
    """
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)

    user = await auth_service.signup(
        email=data.email,
        password=data.password,
        full_name=data.full_name,
        account_type=data.account_type,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return SignupResponse(
        user=user,
        message="Account created successfully. Please check your email to verify your account.",
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    data: LoginRequest,
):
    """
    Login with email and password.

    Returns access token and refresh token.
    """
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)

    result = await auth_service.login(
        email=data.email,
        password=data.password,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return LoginResponse(
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        user=result["user"],
    )


@router.get("/me", response_model=UserWithPermissions)
async def get_me(
    user: dict = Depends(get_current_user),
    org_context: dict = Depends(get_current_org_context),
):
    """
    Get current user profile with permissions.

    Returns user data including:
    - Basic profile information
    - Organization membership
    - Permissions by module
    """
    user_id = UUID(user["id"])
    org_id = UUID(org_context["org_id"]) if org_context.get("org_id") else None

    user_with_perms = await auth_service.get_current_user_with_permissions(user_id, org_id)

    return user_with_perms


@router.post("/logout")
async def logout(
    user: dict = Depends(get_current_user),
):
    """
    Logout current user.

    Note: With JWT, logout is primarily handled on the client side
    by removing the tokens. This endpoint is for audit logging.
    """
    return {
        "success": True,
        "message": "Logged out successfully",
    }


@router.get("/verify-invite/{token}", response_model=VerifyInviteResponse)
async def verify_invite(token: str):
    """
    Verify an invitation token and check if the user already exists.
    
    This endpoint is used by the frontend to determine whether to:
    - Show a signup form (if user doesn't exist)
    - Redirect to login (if user already has an account)
    
    Returns invitation details including organization name and whether user exists.
    """
    result = await auth_service.verify_invitation(token)
    return VerifyInviteResponse(**result)


@router.post("/accept-invite", response_model=LoginResponse)
async def accept_invite(
    request: Request,
    data: AcceptInvitationRequest,
    current_user: dict | None = Depends(get_current_user_optional),
):
    """
    Accept an invitation to join an organization.
    """
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)

    result = await auth_service.accept_invitation(
        invite_token=data.token,
        email=data.email,
        password=data.password,
        full_name=data.full_name,
        ip_address=ip_address,
        user_agent=user_agent,
        current_user=current_user,
    )

    return LoginResponse(
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        user=result["user"],
    )


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

