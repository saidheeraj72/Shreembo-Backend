"""
FastAPI dependencies for request handling.
"""
from typing import Optional
from uuid import UUID
from fastapi import Depends, HTTPException, status, Header, Request
from src.core.database import db
from src.core.security import verify_supabase_jwt
from src.core.exceptions import AuthenticationError, AuthorizationError


async def get_current_user_id(
    authorization: str = Header(None),
) -> UUID:
    """
    Extract and verify user ID from JWT token.

    Args:
        authorization: Authorization header with Bearer token

    Returns:
        User UUID

    Raises:
        HTTPException: If token is invalid or missing
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.replace("Bearer ", "")
    payload = verify_supabase_jwt(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    return UUID(user_id)


async def get_current_user(
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Get current user profile.

    Args:
        user_id: Current user ID

    Returns:
        User profile data

    Raises:
        HTTPException: If user not found
    """
    response = (
        db.admin.table("profiles")
        .select("*")
        .eq("id", str(user_id))
        .single()
        .execute()
    )

    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return response.data


async def get_current_user_optional(
    authorization: str = Header(None),
) -> Optional[dict]:
    """
    Get current user if authenticated, else None.

    Args:
        authorization: Authorization header

    Returns:
        User profile data or None
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    try:
        token = authorization.replace("Bearer ", "")
        payload = verify_supabase_jwt(token)
        if not payload or not payload.get("sub"):
            return None
        
        user_id = payload.get("sub")
        response = (
            db.admin.table("profiles")
            .select("*")
            .eq("id", str(user_id))
            .single()
            .execute()
        )
        return response.data
    except Exception:
        return None


async def get_current_org_context(
    user: dict = Depends(get_current_user),
) -> dict:
    """
    Get current user's organization context.

    Args:
        user: Current user data

    Returns:
        Organization context dict with org_id and role

    Raises:
        HTTPException: If user not part of org
    """
    org_id = user.get("org_id")

    if not org_id:
        # Personal account or no organization assigned
        return {
            "org_id": None,
            "account_type": user.get("account_type", "personal"),
            "role": None,
        }

    # Get organization membership (optional - user might not have membership record yet)
    try:
        response = (
            db.admin.table("organization_members")
            .select("*, roles(*)")
            .eq("org_id", org_id)
            .eq("user_id", str(user["id"]))
            .eq("status", "active")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        print(f"Error fetching org membership: {e}")
        # If error occurs, assume user is part of org but without membership record
        return {
            "org_id": org_id,
            "account_type": "organization",
            "role": None,
            "member_id": None,
        }

    # If user has org_id but no membership record, they're still part of the org
    # (happens when auto-assigned via domain matching)
    if not response or not response.data:
        return {
            "org_id": org_id,
            "account_type": "organization",
            "role": None,  # No specific role assigned yet
            "member_id": None,
        }

    return {
        "org_id": org_id,
        "account_type": "organization",
        "role": response.data.get("roles"),
        "member_id": response.data.get("id"),
    }


async def require_org_member(
    org_context: dict = Depends(get_current_org_context),
) -> dict:
    """
    Require user to be an organization member.

    Args:
        org_context: Organization context

    Returns:
        Organization context

    Raises:
        HTTPException: If not org member
    """
    if org_context["account_type"] != "organization":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization membership required",
        )
    return org_context


async def verify_super_admin(
    user: dict = Depends(get_current_user),
) -> bool:
    """
    Verify if user is a super admin.

    Args:
        user: Current user data

    Returns:
        True if super admin

    Raises:
        HTTPException: If not super admin
    """
    response = (
        db.admin.table("super_admins")
        .select("id")
        .eq("email", user["email"])
        .eq("is_active", True)
        .single()
        .execute()
    )

    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )

    return True


def get_client_ip(request: Request) -> str:
    """
    Get client IP address from request.

    Args:
        request: FastAPI request

    Returns:
        Client IP address
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_user_agent(request: Request) -> str:
    """
    Get user agent from request.

    Args:
        request: FastAPI request

    Returns:
        User agent string
    """
    return request.headers.get("User-Agent", "unknown")
