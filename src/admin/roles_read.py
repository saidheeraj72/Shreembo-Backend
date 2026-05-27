"""Auto-split admin service part."""
from typing import List
from uuid import UUID

from src.core.database import db
from src.core.exceptions import NotFoundError


class AdminRolesReadMixin:
    @staticmethod
    async def list_roles(org_id: UUID) -> List[dict]:
        """
        List all roles for an organization.

        Args:
            org_id: Organization UUID

        Returns:
            List of roles with permission and user counts
        """
        response = (
            db.admin.table("roles")
            .select("*")
            .eq("org_id", str(org_id))
            .order("priority", desc=True)
            .execute()
        )

        role_ids = [role["id"] for role in response.data]
        if not role_ids:
            return []

        # Batch fetch all role permissions in one query
        all_perms_response = (
            db.admin.table("role_permissions")
            .select("role_id, permission_id")
            .in_("role_id", role_ids)
            .execute()
        )

        perms_by_role: dict[str, list] = {}
        for p in (all_perms_response.data or []):
            perms_by_role.setdefault(p["role_id"], []).append(p["permission_id"])

        # Batch fetch user counts per role in one query
        all_members_response = (
            db.admin.table("organization_members")
            .select("role_id")
            .in_("role_id", role_ids)
            .eq("status", "active")
            .execute()
        )

        user_count_by_role: dict[str, int] = {}
        for m in (all_members_response.data or []):
            user_count_by_role[m["role_id"]] = user_count_by_role.get(m["role_id"], 0) + 1

        roles = []
        for role in response.data:
            permission_ids = perms_by_role.get(role["id"], [])
            roles.append({
                **role,
                "permission_count": len(permission_ids),
                "permission_ids": permission_ids,
                "user_count": user_count_by_role.get(role["id"], 0),
            })
        return roles

    @staticmethod
    async def get_role_with_permissions(org_id: UUID, role_id: UUID) -> dict:
        """
        Get a role with its full permission list.

        Args:
            org_id: Organization UUID
            role_id: Role UUID

        Returns:
            Role with permissions

        Raises:
            NotFoundError: If role not found
        """
        role_response = (
            db.admin.table("roles")
            .select("*")
            .eq("id", str(role_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not role_response.data:
            raise NotFoundError("Role not found")

        perms_response = (
            db.admin.table("role_permissions")
            .select("permission_id, permissions(*, permission_modules(*))")
            .eq("role_id", str(role_id))
            .execute()
        )

        user_count = (
            db.admin.table("organization_members")
            .select("user_id", count="exact")
            .eq("role_id", str(role_id))
            .eq("status", "active")
            .execute()
        )

        permissions = []
        permission_ids = []

        for p in perms_response.data or []:
            permission_ids.append(p["permission_id"])
            if p.get("permissions"):
                permissions.append({
                    **p["permissions"],
                    "module": p["permissions"].get("permission_modules", {}),
                })

        return {
            **role_response.data,
            "permissions": permissions,
            "permission_ids": permission_ids,
            "permission_count": len(permissions),
            "user_count": user_count.count or 0,
        }

