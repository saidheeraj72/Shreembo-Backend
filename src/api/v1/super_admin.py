"""
Super Admin API endpoints.
"""
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException

from src.models.super_admin import SuperAdmin, SuperAdminVerifyResponse
from src.models.user import UserProfile
from src.services.super_admin_service import super_admin_service
from src.services.limit_service import limit_service
from src.api.deps.permissions import require_super_admin
from src.core.dependencies import get_current_user

router = APIRouter()


@router.get("/verify", response_model=SuperAdminVerifyResponse)
async def verify_super_admin_access(
    user: dict = Depends(get_current_user),
):
    """
    Verify if current user is a super admin.

    This endpoint can be called by any authenticated user to check
    if they have super admin privileges.

    Returns:
    - **is_super_admin**: Boolean indicating super admin status
    - **email**: User's email address
    """
    email = user.get("email")
    is_super_admin = await super_admin_service.verify_super_admin(email)

    return SuperAdminVerifyResponse(
        is_super_admin=is_super_admin,
        email=email,
    )


@router.get("/list", response_model=List[SuperAdmin])
async def list_super_admins(
    _: dict = Depends(require_super_admin),
):
    """
    List all super admins.

    **Requires:** Super admin access

    Returns list of all super admins with their details.
    """
    super_admins = await super_admin_service.list_super_admins()
    return super_admins


@router.get("/dashboard")
async def get_super_admin_dashboard(
    user: dict = Depends(require_super_admin),
):
    """
    Get super admin dashboard data.

    **Requires:** Super admin access

    Returns:
    - Platform statistics
    - Recent activity
    - List of all organizations
    - System health
    """
    from src.core.database import db

    # Get organization count
    orgs_response = (
        db.admin.table("organizations")
        .select("id", count="exact")
        .execute()
    )

    # Get user count
    users_response = (
        db.admin.table("profiles")
        .select("id", count="exact")
        .execute()
    )

    # Get total files
    files_response = (
        db.admin.table("storage_nodes")
        .select("id", count="exact")
        .eq("node_type", "file")
        .execute()
    )

    # Get all organizations with member count
    all_orgs_response = (
        db.admin.table("organizations")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )

    # Get member counts for each organization
    organizations = []
    for org in all_orgs_response.data:
        # Count members for this org
        member_count_response = (
            db.admin.table("profiles")
            .select("id", count="exact")
            .eq("org_id", org["id"])
            .execute()
        )

        organizations.append({
            **org,
            "member_count": member_count_response.count or 0,
        })

    return {
        "statistics": {
            "total_organizations": orgs_response.count or 0,
            "total_users": users_response.count or 0,
            "total_files": files_response.count or 0,
        },
        "organizations": organizations,
        "super_admin": {
            "email": user.get("email"),
            "full_name": user.get("full_name"),
        },
    }


@router.get("/organizations")
async def list_all_organizations(
    _: dict = Depends(require_super_admin),
):
    """
    List all organizations in the platform.

    **Requires:** Super admin access

    Returns list of all organizations with statistics.
    """
    from src.core.database import db

    # Get all organizations with member count
    response = (
        db.admin.table("organizations")
        .select("*, organization_members(count)")
        .order("created_at", desc=True)
        .execute()
    )

    # Format the response
    organizations = []
    for org in response.data:
        member_count = 0
        if org.get("organization_members"):
            member_count = len(org["organization_members"])

        organizations.append({
            **org,
            "member_count": member_count,
        })

    return {
        "total": len(organizations),
        "organizations": organizations,
    }


@router.get("/users")
async def list_all_users(
    _: dict = Depends(require_super_admin),
):
    """
    List all users in the platform.

    **Requires:** Super admin access

    Returns list of all users with their organization membership.
    """
    from src.core.database import db

    response = (
        db.admin.table("profiles")
        .select("*, organizations(name)")
        .order("created_at", desc=True)
        .execute()
    )

    return {
        "total": len(response.data),
        "users": response.data,
    }


@router.get("/audit-logs")
async def get_platform_audit_logs(
    _: dict = Depends(require_super_admin),
    limit: int = 100,
):
    """
    Get platform-wide audit logs.

    **Requires:** Super admin access

    Query Parameters:
    - **limit**: Maximum number of logs to return (default: 100)
    """
    from src.core.database import db

    response = (
        db.admin.table("audit_logs")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )

    return {
        "total": len(response.data),
        "logs": response.data,
    }


@router.get("/stats")
async def get_platform_statistics(
    _: dict = Depends(require_super_admin),
):
    """
    Get comprehensive platform statistics.

    **Requires:** Super admin access

    Returns detailed statistics about the entire platform.
    """
    from src.core.database import db

    # Organizations by plan type
    orgs_by_plan = (
        db.admin.table("organizations")
        .select("plan_type", count="exact")
        .execute()
    )

    # Users by account type
    users_by_type = (
        db.admin.table("profiles")
        .select("account_type", count="exact")
        .execute()
    )

    # Active vs inactive users
    active_users = (
        db.admin.table("profiles")
        .select("id", count="exact")
        .eq("status", "active")
        .execute()
    )

    # Storage usage
    storage_usage = (
        db.admin.table("storage_nodes")
        .select("file_size")
        .eq("node_type", "file")
        .eq("status", "active")
        .execute()
    )

    total_storage_bytes = sum(
        node.get("file_size", 0) or 0 for node in storage_usage.data
    )
    total_storage_gb = round(total_storage_bytes / (1024**3), 2)

    return {
        "organizations": {
            "total": orgs_by_plan.count or 0,
            "by_plan": {},  # TODO: Group by plan type
        },
        "users": {
            "total": users_by_type.count or 0,
            "active": active_users.count or 0,
            "by_type": {},  # TODO: Group by account type
        },
        "storage": {
            "total_files": len(storage_usage.data),
            "total_storage_gb": total_storage_gb,
            "total_storage_bytes": total_storage_bytes,
        },
    }


@router.get("/organization-usage")
async def get_organization_usage(
    _: dict = Depends(require_super_admin),
):
    """
    Get usage statistics for all organizations.

    **Requires:** Super admin access

    Returns usage statistics (tokens, chat, RAG) for each organization.
    """
    from src.core.database import db

    # Get all organizations
    orgs_response = (
        db.admin.table("organizations")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )

    organizations_with_usage = []

    for org in orgs_response.data:
        org_id = UUID(org["id"])

        # Get usage stats for this organization
        try:
            usage_stats = await limit_service.get_usage_stats("organization", org_id)
        except Exception:
            # If stats aren't available, use defaults
            usage_stats = None

        organizations_with_usage.append({
            **org,
            "monthly_tokens_used": usage_stats.monthly_tokens_used if usage_stats else 0,
            "monthly_tokens_limit": usage_stats.monthly_tokens_limit if usage_stats else 1000000,
            "daily_chat_requests_used": usage_stats.daily_chat_requests_used if usage_stats else 0,
            "daily_chat_requests_limit": usage_stats.daily_chat_requests_limit if usage_stats else 1000,
            "daily_rag_requests_used": usage_stats.daily_rag_requests_used if usage_stats else 0,
            "daily_rag_requests_limit": usage_stats.daily_rag_requests_limit if usage_stats else 500,
        })

    return {
        "total": len(organizations_with_usage),
        "organizations": organizations_with_usage,
    }


@router.get("/organization-requests")
async def list_organization_requests(
    status: str | None = None,
    _: dict = Depends(require_super_admin),
):
    """
    List all organization requests.

    **Requires:** Super admin access

    Query Parameters:
    - **status**: Filter by status (pending, approved, rejected)

    Returns list of organization requests.
    """
    from src.core.database import db

    query = db.admin.table("organization_requests").select("*")

    if status:
        query = query.eq("status", status)

    response = query.order("created_at", desc=True).execute()

    return {
        "total": len(response.data),
        "requests": response.data,
    }


@router.post("/organization-requests/{request_id}/approve")
async def approve_organization_request(
    request_id: str,
    _: dict = Depends(require_super_admin),
):
    """
    Approve an organization request and create the organization.

    **Requires:** Super admin access

    This will:
    1. Create the organization
    2. Create system roles (Owner, Admin, Member) for the organization
    3. Assign all permissions to the Owner role
    4. Add the requester to organization_members with the Owner role
    5. Set the requester as the owner in the organization
    6. Update the request status to approved
    7. Activate the user's profile
    """
    from src.core.database import db
    from datetime import datetime, timedelta
    from uuid import UUID

    print("\n" + "="*80)
    print("🔵 ORGANIZATION REQUEST APPROVAL STARTED")
    print("="*80)
    print(f"📥 Request ID received from frontend: {request_id}")

    # Get the request
    print(f"\n1️⃣ Fetching organization request from Supabase...")
    request_response = (
        db.admin.table("organization_requests")
        .select("*")
        .eq("id", request_id)
        .single()
        .execute()
    )

    print(f"   ✅ Supabase response received")
    print(f"   📊 Request data: {request_response.data}")

    if not request_response.data:
        print("   ❌ ERROR: Organization request not found in database")
        raise HTTPException(status_code=404, detail="Organization request not found")

    request_data = request_response.data
    print(f"   ℹ️ Request details:")
    print(f"      - User ID: {request_data['user_id']}")
    print(f"      - User Email: {request_data['user_email']}")
    print(f"      - User Name: {request_data['user_full_name']}")
    print(f"      - Org Name: {request_data['org_name']}")
    print(f"      - Current Status: {request_data['status']}")

    if request_data["status"] != "pending":
        print(f"   ❌ ERROR: Request status is '{request_data['status']}', not 'pending'")
        raise HTTPException(
            status_code=400,
            detail=f"Request already {request_data['status']}"
        )

    # Create organization
    org_slug = request_data["org_name"].lower().replace(" ", "-")
    trial_ends = datetime.utcnow() + timedelta(days=30)  # 30-day trial for orgs

    # Extract domain from user's email
    user_email = request_data["user_email"]
    email_domain = user_email.split("@")[1] if "@" in user_email else None

    print(f"   📧 Extracted domain from email: {email_domain}")

    org_data = {
        "name": request_data["org_name"],
        "slug": org_slug,
        "domain": email_domain,  # Use email domain instead of org_domain
        "plan_type": "free",
        "subscription_status": "trial",
        "trial_ends_at": trial_ends.isoformat(),
        "owner_id": request_data["user_id"],
        "is_active": True,
    }

    print(f"\n2️⃣ Creating organization in Supabase...")
    print(f"   📤 Organization data to insert: {org_data}")
    org_response = db.admin.table("organizations").insert(org_data).execute()

    print(f"   ✅ Supabase insert response: {org_response.data}")

    if not org_response.data:
        print("   ❌ ERROR: Failed to create organization")
        raise HTTPException(status_code=500, detail="Failed to create organization")

    org_id = org_response.data[0]["id"]
    print(f"   ✅ Organization created with ID: {org_id}")

    # Create system roles for the organization
    print(f"\n3️⃣ Creating system roles for organization...")

    # Get all permissions to assign to owner role
    all_permissions_response = db.admin.table("permissions").select("id").execute()
    all_permission_ids = [p["id"] for p in all_permissions_response.data]
    print(f"   📊 Found {len(all_permission_ids)} total permissions in system")

    # Create Owner role with all permissions
    owner_role_data = {
        "org_id": org_id,
        "name": "Owner",
        "slug": "owner",
        "description": "Organization owner with full access to all features",
        "is_system_role": True,
        "is_custom_role": False,
        "priority": 1000,  # Highest priority
        "color": "#dc2626",  # Red color
    }
    print(f"   📤 Creating Owner role: {owner_role_data}")
    owner_role_response = db.admin.table("roles").insert(owner_role_data).execute()

    if not owner_role_response.data:
        print("   ❌ ERROR: Failed to create Owner role")
        raise HTTPException(status_code=500, detail="Failed to create Owner role")

    owner_role_id = owner_role_response.data[0]["id"]
    print(f"   ✅ Owner role created with ID: {owner_role_id}")

    # Assign all permissions to Owner role
    print(f"   🔐 Assigning all {len(all_permission_ids)} permissions to Owner role...")
    role_permissions = [
        {"role_id": owner_role_id, "permission_id": perm_id}
        for perm_id in all_permission_ids
    ]
    db.admin.table("role_permissions").insert(role_permissions).execute()
    print(f"   ✅ All permissions assigned to Owner role")

    # Create Admin role (subset of permissions)
    admin_role_data = {
        "org_id": org_id,
        "name": "Admin",
        "slug": "admin",
        "description": "Administrator with most permissions except critical settings",
        "is_system_role": True,
        "is_custom_role": False,
        "priority": 900,
        "color": "#f59e0b",  # Orange color
    }
    db.admin.table("roles").insert(admin_role_data).execute()
    print(f"   ✅ Admin role created")

    # Create Member role (basic permissions)
    member_role_data = {
        "org_id": org_id,
        "name": "Member",
        "slug": "member",
        "description": "Basic member with view and edit permissions",
        "is_system_role": True,
        "is_custom_role": False,
        "priority": 100,
        "color": "#3b82f6",  # Blue color
    }
    db.admin.table("roles").insert(member_role_data).execute()
    print(f"   ✅ Member role created")

    # Add user to organization_members with Owner role
    print(f"\n4️⃣ Adding user to organization_members table...")
    member_data = {
        "org_id": org_id,
        "user_id": request_data["user_id"],
        "role_id": owner_role_id,
        "status": "active",
        "title": "Owner",
        "joined_at": datetime.utcnow().isoformat(),
    }
    print(f"   📤 Organization member data: {member_data}")
    member_response = db.admin.table("organization_members").insert(member_data).execute()

    if not member_response.data:
        print("   ❌ ERROR: Failed to add user to organization_members")
        raise HTTPException(status_code=500, detail="Failed to add user as organization member")

    print(f"   ✅ User added to organization_members with Owner role")

    # Update user profile to link to org and activate
    print(f"\n5️⃣ Updating user profile in Supabase...")
    profile_update = {
        "org_id": org_id,
        "status": "active",
    }
    print(f"   📤 Profile update data: {profile_update}")
    print(f"   🔍 Updating profile for user_id: {request_data['user_id']}")

    profile_response = db.admin.table("profiles").update(profile_update).eq("id", request_data["user_id"]).execute()
    print(f"   ✅ Profile updated: {profile_response.data}")

    # Update request status
    print(f"\n6️⃣ Updating request status in Supabase...")
    request_update = {
        "status": "approved",
        "reviewed_at": datetime.utcnow().isoformat(),
    }
    print(f"   📤 Request update data: {request_update}")

    request_update_response = db.admin.table("organization_requests").update(request_update).eq("id", request_id).execute()
    print(f"   ✅ Request status updated: {request_update_response.data}")

    # Log the approval
    print(f"\n7️⃣ Creating audit log...")
    from src.services.audit_service import audit_service
    from src.models.audit import AuditAction

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
    print(f"   ✅ Audit log created")

    # Send organization approval email
    print(f"\n8️⃣ Sending organization approval email...")
    from src.services.email_service import email_service
    email_service.send_organization_approved_email(
        to_email=request_data["user_email"],
        user_name=request_data["user_full_name"],
        org_name=request_data["org_name"],
    )
    print(f"   ✅ Organization approval email sent")

    response_data = {
        "message": "Organization request approved",
        "organization_id": org_id,
        "organization_name": request_data["org_name"],
    }

    print(f"\n📤 Sending response to frontend: {response_data}")
    print("="*80)
    print("✅ ORGANIZATION REQUEST APPROVAL COMPLETED")
    print("="*80 + "\n")

    return response_data


@router.post("/organization-requests/{request_id}/reject")
async def reject_organization_request(
    request_id: str,
    rejection_reason: str,
    _: dict = Depends(require_super_admin),
):
    """
    Reject an organization request.

    **Requires:** Super admin access

    Body:
    - **rejection_reason**: Reason for rejection
    """
    from src.core.database import db
    from datetime import datetime

    # Get the request
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
        raise HTTPException(
            status_code=400,
            detail=f"Request already {request_data['status']}"
        )

    # Update request status
    db.admin.table("organization_requests").update({
        "status": "rejected",
        "rejection_reason": rejection_reason,
        "reviewed_at": datetime.utcnow().isoformat(),
    }).eq("id", request_id).execute()

    return {
        "message": "Organization request rejected",
        "request_id": request_id,
    }
