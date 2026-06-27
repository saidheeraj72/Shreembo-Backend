"""Auto-split admin service part."""
from typing import List, Optional
from uuid import UUID

from src.core.database import db
from src.core.exceptions import NotFoundError
from src.audit.service import audit_service
from src.models.audit import AuditAction


class AdminUsersMixin:
    @staticmethod
    async def list_org_users(
        org_id: UUID,
        status: Optional[str] = None,
    ) -> List[dict]:
        """
        List all organization members with their roles.

        Args:
            org_id: Organization UUID
            status: Optional filter by member status

        Returns:
            List of members with user profiles and roles
        """
        query = (
            db.admin.table("organization_members")
            .select("*, profiles!organization_members_user_id_fkey(id, email, full_name, display_name, avatar_url, status, last_active_at), roles(id, name, slug, color, icon)")
            .eq("org_id", str(org_id))
            .order("joined_at", desc=True)
        )

        if status:
            query = query.eq("status", status)

        response = query.execute()

        user_ids = [member["user_id"] for member in response.data]
        role_ids = list(set(member["role_id"] for member in response.data if member.get("role_id")))

        # Batch fetch all branch assignments in one query
        branches_by_user: dict[str, list] = {}
        if user_ids:
            all_branches_response = (
                db.admin.table("user_branches")
                .select("*, branches(id, name, code)")
                .in_("user_id", user_ids)
                .execute()
            )
            for b in (all_branches_response.data or []):
                branches_by_user.setdefault(b["user_id"], []).append(b)

        # Batch fetch all role permissions in one query
        perms_by_role: dict[str, list] = {}
        if role_ids:
            all_role_perms_response = (
                db.admin.table("role_permissions")
                .select("role_id, permission_id")
                .in_("role_id", role_ids)
                .execute()
            )
            for p in (all_role_perms_response.data or []):
                perms_by_role.setdefault(p["role_id"], []).append(p["permission_id"])

        users = []
        for member in response.data:
            role_data = member.get("roles")
            if role_data and member.get("role_id"):
                permission_ids = perms_by_role.get(member["role_id"], [])
                role_data = {
                    **role_data,
                    "permission_ids": permission_ids,
                    "permission_count": len(permission_ids),
                }

            user_branches = branches_by_user.get(member["user_id"], [])
            users.append({
                **member,
                "user": member.get("profiles"),
                "role": role_data,
                "branches": [
                    {**b["branches"], "is_primary": b["is_primary"]}
                    for b in user_branches
                ],
            })
        return users

    @staticmethod
    async def get_user_details(org_id: UUID, user_id: UUID) -> dict:
        """
        Get detailed user information within an organization.

        Args:
            org_id: Organization UUID
            user_id: User UUID

        Returns:
            User details with role, permissions, and branches

        Raises:
            NotFoundError: If user not found in org
        """
        member_response = (
            db.admin.table("organization_members")
            .select("*, profiles!organization_members_user_id_fkey(*), roles(*)")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .single()
            .execute()
        )

        if not member_response.data:
            raise NotFoundError("User not found in organization")

        from src.access.permission import permission_service
        permissions = await permission_service.get_user_permissions(user_id, org_id)

        branches_response = (
            db.admin.table("user_branches")
            .select("*, branches(id, name, code, branch_type)")
            .eq("user_id", str(user_id))
            .execute()
        )

        return {
            **member_response.data,
            "user": member_response.data.get("profiles"),
            "role": member_response.data.get("roles"),
            "permissions": permissions,
            "branches": [
                {**b["branches"], "is_primary": b["is_primary"]}
                for b in branches_response.data
            ],
        }

    @staticmethod
    async def update_member(
        org_id: UUID,
        user_id: UUID,
        data: dict,
        updated_by: UUID,
    ) -> dict:
        """
        Update organization member details.

        Args:
            org_id: Organization UUID
            user_id: User UUID
            data: Update data (title, department, employee_id, status)
            updated_by: User making the update

        Returns:
            Updated member record
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

        # Update member
        update_data = {k: v for k, v in data.items() if v is not None}

        response = (
            db.admin.table("organization_members")
            .update(update_data)
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .execute()
        )

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=updated_by,
            action=AuditAction.UPDATE,
            resource_type="member",
            resource_id=user_id,
            resource_name=existing.data["profiles"]["full_name"] or existing.data["profiles"]["email"],
            description=f"Updated member: {existing.data['profiles']['email']}",
        )

        return response.data[0] if response.data else {}

