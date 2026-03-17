from typing import Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from uuid import UUID

from src.models.auth import LoginRequest, LoginResponse, SignupRequest, SignupResponse, AcceptInvitationRequest, VerifyInviteResponse
from src.models.user import UserWithPermissions
from src.auth.service import auth_service
from src.core.dependencies import get_current_user, get_current_org_context, get_client_ip, get_user_agent, get_current_user_optional

router = APIRouter()

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

