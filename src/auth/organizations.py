"""Auto-split auth service part."""
from datetime import datetime, timedelta
from typing import Optional, Dict
from uuid import UUID
import logging

from src.core.database import db
from src.core.exceptions import AuthenticationError, ConflictError, NotFoundError, ValidationError
from src.audit.service import audit_service
from src.models.audit import AuditAction

logger = logging.getLogger(__name__)


class AuthOrganizationsMixin:
    async def get_user_organizations(user_id: UUID) -> list:
        """
        Get all organizations the user is a member of.

        Args:
            user_id: User ID

        Returns:
            List of organizations with membership details
        """
        # Get organization memberships
        memberships_response = (
            db.admin.table("organization_members")
            .select("org_id, status, role_id, joined_at, title, department")
            .eq("user_id", str(user_id))
            .eq("status", "active")
            .execute()
        )

        if not memberships_response.data:
            return []

        # Get organization details for each membership
        organizations = []
        for membership in memberships_response.data:
            org_response = (
                db.admin.table("organizations")
                .select("id, name, slug, logo_url, domain, is_active")
                .eq("id", membership["org_id"])
                .eq("is_active", True)
                .maybe_single()
                .execute()
            )

            if org_response and org_response.data:
                org_data = org_response.data
                org_data["role_id"] = membership["role_id"]
                org_data["joined_at"] = membership["joined_at"]
                org_data["title"] = membership["title"]
                org_data["department"] = membership["department"]
                organizations.append(org_data)

        return organizations

    @staticmethod
    async def switch_organization(user_id: UUID, target_org_id: Optional[UUID]) -> Dict:
        """
        Switch user's active organization context.

        Args:
            user_id: User ID
            target_org_id: Target organization ID (None for personal workspace)

        Returns:
            Updated user data with new org context and permissions
        """
        from src.access.permission import permission_service
        from src.super_admin.service import super_admin_service
        from src.core.exceptions import AuthorizationError, NotFoundError

        # Get user profile
        profile_response = (
            db.admin.table("profiles")
            .select("*")
            .eq("id", str(user_id))
            .single()
            .execute()
        )

        if not profile_response.data:
            raise NotFoundError("User not found")

        user_profile = profile_response.data

        # If switching to personal workspace (null org)
        if target_org_id is None:
            # Update profile to personal context
            db.admin.table("profiles").update({
                "org_id": None,
                "account_type": "personal"
            }).eq("id", str(user_id)).execute()

            # Refresh profile
            updated_profile = (
                db.admin.table("profiles")
                .select("*")
                .eq("id", str(user_id))
                .single()
                .execute()
            )

            user_data = updated_profile.data
            user_data["permissions"] = {}
            
            # Check super admin status
            is_super_admin = await super_admin_service.verify_super_admin(user_data["email"])
            user_data["is_super_admin"] = is_super_admin
            if is_super_admin:
                user_data["permissions"]["super_admin"] = {"access": True}

            return user_data

        # Verify user is a member of the target organization
        membership_response = (
            db.admin.table("organization_members")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("org_id", str(target_org_id))
            .eq("status", "active")
            .maybe_single()
            .execute()
        )

        if not membership_response.data:
            raise AuthorizationError("You are not a member of this organization")

        # Verify organization is active
        org_response = (
            db.admin.table("organizations")
            .select("id, name, is_active")
            .eq("id", str(target_org_id))
            .eq("is_active", True)
            .maybe_single()
            .execute()
        )

        if not org_response.data:
            raise NotFoundError("Organization not found or inactive")

        # Update profile with new org context
        db.admin.table("profiles").update({
            "org_id": str(target_org_id),
            "account_type": "organization"
        }).eq("id", str(user_id)).execute()

        # Get updated profile with permissions
        return await AuthService.get_current_user_with_permissions(user_id, target_org_id)

