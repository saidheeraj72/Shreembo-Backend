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


class AdminBranchAssignmentsMixin:
    @staticmethod
    async def assign_user_to_branch(
        org_id: UUID,
        branch_id: UUID,
        user_id: UUID,
        assigned_by: UUID,
        is_primary: bool = False,
    ) -> dict:
        """
        Assign a user to a branch.

        Args:
            org_id: Organization UUID
            branch_id: Branch UUID
            user_id: User to assign
            assigned_by: User making the assignment
            is_primary: Whether this is the user's primary branch

        Returns:
            Assignment record
        """
        # Verify branch belongs to org
        branch = (
            db.admin.table("branches")
            .select("name")
            .eq("id", str(branch_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not branch.data:
            raise NotFoundError("Branch not found")

        # Verify user belongs to org
        member = (
            db.admin.table("organization_members")
            .select("id")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .eq("status", "active")
            .maybe_single()
            .execute()
        )

        if not member or not member.data:
            raise NotFoundError("User is not a member of this organization")

        # If setting as primary, remove primary from other branches
        if is_primary:
            db.admin.table("user_branches").update({
                "is_primary": False,
            }).eq("user_id", str(user_id)).execute()

            # Also update profile's primary_branch_id
            db.admin.table("profiles").update({
                "primary_branch_id": str(branch_id),
            }).eq("id", str(user_id)).execute()

        # Upsert assignment
        response = (
            db.admin.table("user_branches")
            .upsert({
                "user_id": str(user_id),
                "branch_id": str(branch_id),
                "is_primary": is_primary,
                "assigned_by": str(assigned_by),
                "assigned_at": datetime.utcnow().isoformat(),
            }, on_conflict="user_id,branch_id")
            .execute()
        )

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=assigned_by,
            action=AuditAction.UPDATE,
            resource_type="branch",
            resource_id=branch_id,
            resource_name=branch.data["name"],
            description=f"Assigned user to branch: {branch.data['name']}",
        )

        return response.data[0] if response.data else {}

    @staticmethod
    async def remove_user_from_branch(
        org_id: UUID,
        branch_id: UUID,
        user_id: UUID,
        removed_by: UUID,
    ) -> bool:
        """
        Remove a user from a branch.

        Args:
            org_id: Organization UUID
            branch_id: Branch UUID
            user_id: User to remove
            removed_by: User making the removal

        Returns:
            True if successful
        """
        # Verify branch belongs to org
        branch = (
            db.admin.table("branches")
            .select("name")
            .eq("id", str(branch_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not branch.data:
            raise NotFoundError("Branch not found")

        # Delete assignment
        db.admin.table("user_branches").delete().eq(
            "user_id", str(user_id)
        ).eq("branch_id", str(branch_id)).execute()

        # If this was primary, clear from profile
        db.admin.table("profiles").update({
            "primary_branch_id": None,
        }).eq("id", str(user_id)).eq("primary_branch_id", str(branch_id)).execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=removed_by,
            action=AuditAction.UPDATE,
            resource_type="branch",
            resource_id=branch_id,
            resource_name=branch.data["name"],
            description=f"Removed user from branch: {branch.data['name']}",
        )

        return True

