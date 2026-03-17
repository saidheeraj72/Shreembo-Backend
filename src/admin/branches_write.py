"""Admin branch update/delete operations."""
from uuid import UUID

from src.core.database import db
from src.core.exceptions import NotFoundError, ConflictError
from src.audit.service import audit_service
from src.models.audit import AuditAction


class AdminBranchesWriteMixin:
    @staticmethod
    async def update_branch(
        org_id: UUID,
        branch_id: UUID,
        data: dict,
        updated_by: UUID,
    ) -> dict:
        """
        Update a branch.

        Args:
            org_id: Organization UUID
            branch_id: Branch UUID
            data: Update data
            updated_by: User updating the branch

        Returns:
            Updated branch

        Raises:
            NotFoundError: If branch not found
        """
        # Verify branch exists and belongs to org
        existing = (
            db.admin.table("branches")
            .select("*")
            .eq("id", str(branch_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Branch not found")

        # Check for duplicate code if code is being updated
        if data.get("code") and data["code"] != existing.data.get("code"):
            code_check = (
                db.admin.table("branches")
                .select("id")
                .eq("org_id", str(org_id))
                .eq("code", data["code"])
                .neq("id", str(branch_id))
                .maybe_single()
                .execute()
            )
            if code_check and code_check.data:
                raise ConflictError(f"Branch with code '{data['code']}' already exists")

        # Update branch
        update_data = {k: str(v) if isinstance(v, UUID) else v for k, v in data.items() if v is not None}

        response = (
            db.admin.table("branches")
            .update(update_data)
            .eq("id", str(branch_id))
            .execute()
        )

        if not response.data:
            raise Exception("Failed to update branch")

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=updated_by,
            action=AuditAction.UPDATE,
            resource_type="branch",
            resource_id=branch_id,
            resource_name=response.data[0]["name"],
            description=f"Updated branch: {response.data[0]['name']}",
            changes={"before": existing.data, "after": response.data[0]},
        )

        return response.data[0]

    @staticmethod
    async def delete_branch(
        org_id: UUID,
        branch_id: UUID,
        deleted_by: UUID,
    ) -> bool:
        """
        Soft delete a branch (set is_active=false).

        Args:
            org_id: Organization UUID
            branch_id: Branch UUID
            deleted_by: User deleting the branch

        Returns:
            True if successful

        Raises:
            NotFoundError: If branch not found
        """
        # Verify branch exists
        existing = (
            db.admin.table("branches")
            .select("name")
            .eq("id", str(branch_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Branch not found")

        # Soft delete
        db.admin.table("branches").update({
            "is_active": False,
        }).eq("id", str(branch_id)).execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=deleted_by,
            action=AuditAction.DELETE,
            resource_type="branch",
            resource_id=branch_id,
            resource_name=existing.data["name"],
            description=f"Deleted branch: {existing.data['name']}",
        )

        return True
