"""Admin branch read/create operations."""
from typing import List
from uuid import UUID

from src.core.database import db
from src.core.exceptions import NotFoundError, ConflictError
from src.audit.service import audit_service
from src.models.audit import AuditAction


class AdminBranchesReadMixin:
    @staticmethod
    async def list_branches(
        org_id: UUID,
        include_inactive: bool = False,
    ) -> List[dict]:
        """
        List all branches for an organization.

        Args:
            org_id: Organization UUID
            include_inactive: Whether to include inactive branches

        Returns:
            List of branches with manager details and user counts
        """
        query = (
            db.admin.table("branches")
            .select("*, profiles!fk_branch_manager(id, full_name, email, avatar_url)")
            .eq("org_id", str(org_id))
            .order("name")
        )

        if not include_inactive:
            query = query.eq("is_active", True)

        response = query.execute()

        branch_ids = [branch["id"] for branch in response.data]

        # Batch fetch all user-branch assignments in one query
        user_count_by_branch: dict[str, int] = {}
        if branch_ids:
            all_user_branches_response = (
                db.admin.table("user_branches")
                .select("branch_id")
                .in_("branch_id", branch_ids)
                .execute()
            )
            for ub in (all_user_branches_response.data or []):
                user_count_by_branch[ub["branch_id"]] = user_count_by_branch.get(ub["branch_id"], 0) + 1

        return [
            {**branch, "manager": branch.get("profiles"), "user_count": user_count_by_branch.get(branch["id"], 0)}
            for branch in response.data
        ]

    @staticmethod
    async def get_branch(org_id: UUID, branch_id: UUID) -> dict:
        """
        Get a single branch with details.

        Args:
            org_id: Organization UUID
            branch_id: Branch UUID

        Returns:
            Branch details with manager and assigned users

        Raises:
            NotFoundError: If branch not found
        """
        response = (
            db.admin.table("branches")
            .select("*, profiles!fk_branch_manager(id, full_name, email, avatar_url)")
            .eq("id", str(branch_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not response.data:
            raise NotFoundError("Branch not found")

        # Get assigned users
        users_response = (
            db.admin.table("user_branches")
            .select("*, profiles(id, full_name, email, avatar_url, status)")
            .eq("branch_id", str(branch_id))
            .execute()
        )

        return {
            **response.data,
            "manager": response.data.get("profiles"),
            "users": [
                {
                    **u["profiles"],
                    "is_primary": u["is_primary"],
                    "assigned_at": u["assigned_at"],
                }
                for u in users_response.data
            ],
            "user_count": len(users_response.data),
        }

    @staticmethod
    async def create_branch(
        org_id: UUID,
        data: dict,
        created_by: UUID,
    ) -> dict:
        """
        Create a new branch.

        Args:
            org_id: Organization UUID
            data: Branch data
            created_by: User creating the branch

        Returns:
            Created branch

        Raises:
            ConflictError: If branch code already exists
        """
        # Check for duplicate code
        if data.get("code"):
            existing = (
                db.admin.table("branches")
                .select("id")
                .eq("org_id", str(org_id))
                .eq("code", data["code"])
                .maybe_single()
                .execute()
            )
            if existing and existing.data:
                raise ConflictError(f"Branch with code '{data['code']}' already exists")

        # Create branch
        branch_data = {
            "org_id": str(org_id),
            **{k: str(v) if isinstance(v, UUID) else v for k, v in data.items()},
        }

        response = db.admin.table("branches").insert(branch_data).execute()

        if not response.data:
            raise Exception("Failed to create branch")

        branch = response.data[0]

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=created_by,
            action=AuditAction.CREATE,
            resource_type="branch",
            resource_id=UUID(branch["id"]),
            resource_name=branch["name"],
            description=f"Created branch: {branch['name']}",
        )

        return branch
