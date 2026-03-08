"""Group delete operation."""
from uuid import UUID

from src.core.database import db
from src.core.exceptions import NotFoundError
from src.audit.service import audit_service
from src.models.audit import AuditAction


class GroupDeleteMixin:
    @staticmethod
    async def delete_group(
        org_id: UUID,
        group_id: UUID,
        deleted_by: UUID,
    ) -> bool:
        """
        Delete a group.

        Args:
            org_id: Organization UUID
            group_id: Group UUID
            deleted_by: User deleting

        Returns:
            True if successful
        """
        # Verify group
        existing = (
            db.admin.table("groups")
            .select("name")
            .eq("id", str(group_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Group not found")

        # Delete
        db.admin.table("groups").delete().eq("id", str(group_id)).execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=deleted_by,
            action=AuditAction.DELETE,
            resource_type="group",
            resource_id=group_id,
            resource_name=existing.data["name"],
            description=f"Deleted group: {existing.data['name']}",
        )

        return True

