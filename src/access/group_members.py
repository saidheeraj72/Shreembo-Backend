"""Auto-split group service part."""
from typing import List
from uuid import UUID
from datetime import datetime

from src.core.database import db
from src.core.exceptions import NotFoundError, ConflictError
from src.audit.service import audit_service
from src.models.audit import AuditAction


class GroupMembersMixin:
    @staticmethod
    async def add_members(
        org_id: UUID,
        group_id: UUID,
        user_ids: List[UUID],
        added_by: UUID,
    ) -> bool:
        """
        Add members to a group.

        Args:
            org_id: Organization UUID
            group_id: Group UUID
            user_ids: List of user UUIDs
            added_by: User adding members

        Returns:
            True if successful
        """
        # Verify group
        group = (
            db.admin.table("groups")
            .select("name")
            .eq("id", str(group_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not group.data:
            raise NotFoundError("Group not found")

        # Add members (ignore duplicates)
        members_data = [
            {
                "group_id": str(group_id),
                "user_id": str(uid),
                "added_by": str(added_by),
            }
            for uid in user_ids
        ]
        
        db.admin.table("group_members").upsert(members_data, on_conflict="group_id,user_id").execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=added_by,
            action=AuditAction.UPDATE,
            resource_type="group",
            resource_id=group_id,
            resource_name=group.data["name"],
            description=f"Added {len(user_ids)} members to group {group.data['name']}",
        )

        return True

    @staticmethod
    async def remove_members(
        org_id: UUID,
        group_id: UUID,
        user_ids: List[UUID],
        removed_by: UUID,
    ) -> bool:
        """
        Remove members from a group.

        Args:
            org_id: Organization UUID
            group_id: Group UUID
            user_ids: List of user UUIDs
            removed_by: User removing members

        Returns:
            True if successful
        """
        # Verify group
        group = (
            db.admin.table("groups")
            .select("name")
            .eq("id", str(group_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not group.data:
            raise NotFoundError("Group not found")

        # Remove members
        user_ids_str = [str(uid) for uid in user_ids]
        db.admin.table("group_members").delete().eq("group_id", str(group_id)).in_("user_id", user_ids_str).execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=removed_by,
            action=AuditAction.UPDATE,
            resource_type="group",
            resource_id=group_id,
            resource_name=group.data["name"],
            description=f"Removed {len(user_ids)} members from group {group.data['name']}",
        )

        return True
