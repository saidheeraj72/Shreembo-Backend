"""Auto-split admin service part."""
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID
import secrets
import logging

from src.core.database import db
from src.core.cache import cache
from src.core.exceptions import NotFoundError, ConflictError, AuthorizationError
from src.audit.service import audit_service
from src.email.service import email_service
from src.models.audit import AuditAction

logger = logging.getLogger(__name__)


class AdminUserRolesMixin:
    @staticmethod
    async def change_user_role(
        org_id: UUID,
        user_id: UUID,
        role_id: UUID,
        changed_by: UUID,
    ) -> dict:
        """
        Change a user's role in the organization.

        Args:
            org_id: Organization UUID
            user_id: User UUID
            role_id: New role UUID
            changed_by: User making the change

        Returns:
            Updated member record
        """
        # Verify member exists
        existing = (
            db.admin.table("organization_members")
            .select("*, profiles(email, full_name), roles(name)")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("User not found in organization")

        # Verify new role belongs to org
        role = (
            db.admin.table("roles")
            .select("name")
            .eq("id", str(role_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not role.data:
            raise NotFoundError("Role not found")

        # Update role
        response = (
            db.admin.table("organization_members")
            .update({"role_id": str(role_id)})
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .execute()
        )

        # Invalidate permission cache
        await cache.delete_pattern(f"permission:{user_id}:*")
        await cache.delete_pattern(f"user_permissions:{user_id}:*")

        # Log audit
        old_role_name = existing.data.get("roles", {}).get("name", "None")
        await audit_service.log(
            org_id=org_id,
            user_id=changed_by,
            action=AuditAction.ROLE_CHANGE,
            resource_type="member",
            resource_id=user_id,
            resource_name=existing.data["profiles"]["full_name"] or existing.data["profiles"]["email"],
            description=f"Changed role from '{old_role_name}' to '{role.data['name']}'",
        )

        return response.data[0] if response.data else {}

    @staticmethod
    async def remove_member(
        org_id: UUID,
        user_id: UUID,
        removed_by: UUID,
    ) -> bool:
        """
        Remove a member from the organization.

        Args:
            org_id: Organization UUID
            user_id: User UUID
            removed_by: User performing the removal

        Returns:
            True if successful
        """
        # Verify member exists
        existing = (
            db.admin.table("organization_members")
            .select("*, profiles(email, full_name)")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("User not found in organization")

        # Check if trying to remove owner
        org = (
            db.admin.table("organizations")
            .select("owner_id")
            .eq("id", str(org_id))
            .single()
            .execute()
        )

        if org.data and org.data.get("owner_id") == str(user_id):
            raise AuthorizationError("Cannot remove organization owner")

        # Update member status to removed
        db.admin.table("organization_members").update({
            "status": "removed",
            "removed_at": datetime.utcnow().isoformat(),
            "removed_by": str(removed_by),
        }).eq("org_id", str(org_id)).eq("user_id", str(user_id)).execute()

        # Remove from branches
        db.admin.table("user_branches").delete().eq("user_id", str(user_id)).execute()

        # Clear org_id from profile
        db.admin.table("profiles").update({
            "org_id": None,
            "primary_branch_id": None,
        }).eq("id", str(user_id)).execute()

        # Invalidate permission cache
        await cache.delete_pattern(f"permission:{user_id}:*")
        await cache.delete_pattern(f"user_permissions:{user_id}:*")

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=removed_by,
            action=AuditAction.DELETE,
            resource_type="member",
            resource_id=user_id,
            resource_name=existing.data["profiles"]["full_name"] or existing.data["profiles"]["email"],
            description=f"Removed member: {existing.data['profiles']['email']}",
        )

        return True

