"""Super-admin organization request approval workflow."""
from datetime import datetime, timedelta
from uuid import UUID
import logging

from fastapi import HTTPException

from src.core.database import db
from src.audit.service import audit_service
from src.email.service import email_service
from src.models.audit import AuditAction

logger = logging.getLogger(__name__)


async def approve_organization_request_workflow(request_id: str) -> dict:
    """Approve an org request and create initial org setup."""
    logger.info("Organization request approval started for request_id: %s", request_id)

    request_response = (
        db.admin.table("organization_requests")
        .select("*")
        .eq("id", request_id)
        .single()
        .execute()
    )
    if not request_response.data:
        raise HTTPException(status_code=404, detail="Organization request not found")

    request_data = request_response.data
    if request_data["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {request_data['status']}")

    org_slug = request_data["org_name"].lower().replace(" ", "-")
    trial_ends = datetime.utcnow() + timedelta(days=30)
    user_email = request_data["user_email"]
    email_domain = user_email.split("@")[1] if "@" in user_email else None

    org_response = db.admin.table("organizations").insert(
        {
            "name": request_data["org_name"],
            "slug": org_slug,
            "domain": email_domain,
            "plan_type": "free",
            "subscription_status": "trial",
            "trial_ends_at": trial_ends.isoformat(),
            "owner_id": request_data["user_id"],
            "is_active": True,
        }
    ).execute()
    if not org_response.data:
        raise HTTPException(status_code=500, detail="Failed to create organization")

    org_id = org_response.data[0]["id"]
    all_permissions_response = db.admin.table("permissions").select("id").execute()
    all_permission_ids = [p["id"] for p in all_permissions_response.data]

    owner_role_response = db.admin.table("roles").insert(
        {
            "org_id": org_id,
            "name": "Owner",
            "slug": "owner",
            "description": "Organization owner with full access to all features",
            "is_system_role": True,
            "is_custom_role": False,
            "priority": 1000,
            "color": "#dc2626",
        }
    ).execute()
    if not owner_role_response.data:
        raise HTTPException(status_code=500, detail="Failed to create Owner role")
    owner_role_id = owner_role_response.data[0]["id"]

    db.admin.table("role_permissions").insert(
        [{"role_id": owner_role_id, "permission_id": perm_id} for perm_id in all_permission_ids]
    ).execute()
    db.admin.table("roles").insert(
        {
            "org_id": org_id,
            "name": "Admin",
            "slug": "admin",
            "description": "Administrator with most permissions except critical settings",
            "is_system_role": True,
            "is_custom_role": False,
            "priority": 900,
            "color": "#f59e0b",
        }
    ).execute()
    db.admin.table("roles").insert(
        {
            "org_id": org_id,
            "name": "Member",
            "slug": "member",
            "description": "Basic member with view and edit permissions",
            "is_system_role": True,
            "is_custom_role": False,
            "priority": 100,
            "color": "#3b82f6",
        }
    ).execute()

    member_response = db.admin.table("organization_members").insert(
        {
            "org_id": org_id,
            "user_id": request_data["user_id"],
            "role_id": owner_role_id,
            "status": "active",
            "title": "Owner",
            "joined_at": datetime.utcnow().isoformat(),
        }
    ).execute()
    if not member_response.data:
        raise HTTPException(status_code=500, detail="Failed to add user as organization member")

    db.admin.table("profiles").update({"org_id": org_id, "status": "active"}).eq(
        "id", request_data["user_id"]
    ).execute()
    db.admin.table("organization_requests").update(
        {"status": "approved", "reviewed_at": datetime.utcnow().isoformat()}
    ).eq("id", request_id).execute()

    await audit_service.log(
        org_id=UUID(org_id),
        user_id=UUID(request_data["user_id"]),
        user_email=request_data["user_email"],
        user_name=request_data["user_full_name"],
        action=AuditAction.CREATE,
        resource_type="organization",
        resource_id=UUID(org_id),
        description=f"Organization created: {request_data['org_name']}",
    )
    email_service.send_organization_approved_email(
        to_email=request_data["user_email"],
        user_name=request_data["user_full_name"],
        org_name=request_data["org_name"],
    )

    return {
        "message": "Organization request approved",
        "organization_id": org_id,
        "organization_name": request_data["org_name"],
    }
