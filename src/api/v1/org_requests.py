"""
Organization request endpoints for users to request organization creation.
"""
from typing import Dict
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr

from src.core.exceptions import ConflictError
from src.core.database import db
from src.services.audit_service import audit_service
from src.models.audit import AuditAction


router = APIRouter()


class OrgRequestCreate(BaseModel):
    """Organization request creation schema."""
    email: EmailStr
    password: str
    full_name: str
    org_name: str
    org_domain: str | None = None
    org_size: str | None = None
    industry: str | None = None


@router.post("/request")
async def create_organization_request(
    data: OrgRequestCreate,
    request: Request,
) -> Dict:
    """
    Submit a request to create a new organization.

    This creates a pending request that requires super admin approval.
    User account is created but organization is not created until approved.

    Args:
        data: Organization request data
        request: FastAPI request object

    Returns:
        Success message with request ID

    Raises:
        ConflictError: If email already registered
    """
    from src.services.auth_service import auth_service

    # Get client info for audit
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    # Check if email already exists
    existing = (
        db.admin.table("profiles")
        .select("id")
        .eq("email", data.email.lower())
        .maybe_single()
        .execute()
    )

    if existing and existing.data:
        raise ConflictError("Email already registered. Please login instead.")

    # Create user account via Supabase Auth
    try:
        auth_response = db.anon.auth.sign_up({
            "email": data.email,
            "password": data.password,
        })
    except Exception as e:
        raise ConflictError(f"Failed to create user: {str(e)}")

    user_id = auth_response.user.id

    # Create basic profile (no subscription fields - will be org member)
    profile_data = {
        "id": user_id,
        "email": data.email.lower(),
        "full_name": data.full_name,
        "account_type": "organization",  # Will be organization user
        "status": "inactive",  # Inactive until org approved
    }

    db.admin.table("profiles").insert(profile_data).execute()

    # Create organization request
    request_data = {
        "user_id": user_id,
        "user_email": data.email.lower(),
        "user_full_name": data.full_name,
        "org_name": data.org_name,
        "org_domain": data.org_domain,
        "org_size": data.org_size,
        "industry": data.industry,
        "status": "pending",
    }

    response = db.admin.table("organization_requests").insert(request_data).execute()

    if not response.data:
        raise Exception("Failed to create organization request")

    request_id = response.data[0]["id"]

    # Log the request
    from uuid import UUID
    await audit_service.log(
        org_id=None,
        user_id=UUID(user_id),
        user_email=data.email,
        user_name=data.full_name,
        action=AuditAction.CREATE,
        resource_type="organization_request",
        resource_id=UUID(request_id),
        description=f"Organization request submitted: {data.org_name}",
        ip_address=client_ip,
        user_agent=user_agent,
    )

    return {
        "message": "Organization request submitted successfully",
        "request_id": request_id,
        "status": "pending",
        "note": "Your request is pending super admin approval. You'll receive an email once it's reviewed.",
    }
