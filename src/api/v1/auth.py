"""
Authentication API endpoints.
"""
from fastapi import APIRouter, Depends, Request
from uuid import UUID

from src.models.auth import LoginRequest, LoginResponse, SignupRequest, SignupResponse, AcceptInvitationRequest
from src.models.user import UserWithPermissions
from src.services.auth_service import auth_service
from src.core.dependencies import get_current_user, get_current_org_context, get_client_ip, get_user_agent

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


@router.post("/accept-invite", response_model=LoginResponse)
async def accept_invite(
    request: Request,
    data: AcceptInvitationRequest,
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
    )

    return LoginResponse(
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        user=result["user"],
    )
