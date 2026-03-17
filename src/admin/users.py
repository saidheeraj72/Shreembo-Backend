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
        logger.info(f"[USERS] Fetching users for org_id: {org_id}, status filter: {status}")

        query = (
            db.admin.table("organization_members")
            .select("*, profiles!organization_members_user_id_fkey(id, email, full_name, display_name, avatar_url, status, last_active_at), roles(id, name, slug, color, icon)")
            .eq("org_id", str(org_id))
            .order("joined_at", desc=True)
        )

        if status:
            query = query.eq("status", status)

        response = query.execute()
        logger.info(f"[USERS] Found {len(response.data)} members")

        # Get branch assignments for each user
        users = []
        for member in response.data:
            logger.info(f"[USERS] Processing user: {member.get('user_id')}, role: {member.get('roles', {}).get('name', 'N/A')}")

            branches_response = (
                db.admin.table("user_branches")
                .select("*, branches(id, name, code)")
                .eq("user_id", member["user_id"])
                .execute()
            )

            # Enrich role data with permission_ids
            role_data = member.get("roles")
            if role_data and member.get("role_id"):
                # Get permission IDs for this role
                role_perms = (
                    db.admin.table("role_permissions")
                    .select("permission_id")
                    .eq("role_id", member["role_id"])
                    .execute()
                )
                permission_ids = [p["permission_id"] for p in (role_perms.data or [])]
                role_data = {
                    **role_data,
                    "permission_ids": permission_ids,
                    "permission_count": len(permission_ids),
                }
                logger.info(f"[USERS] User {member.get('user_id')}: enriched role with {len(permission_ids)} permissions")

            user_data = {
                **member,
                "user": member.get("profiles"),
                "role": role_data,
                "branches": [
                    {**b["branches"], "is_primary": b["is_primary"]}
                    for b in branches_response.data
                ],
            }

            logger.info(f"[USERS] User {member.get('user_id')}: role_id={member.get('role_id')}, role_name={user_data.get('role', {}).get('name', 'N/A')}")

            users.append(user_data)

        logger.info(f"[USERS] Returning {len(users)} users")
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
        logger.info(f"[USER_DETAIL] Fetching user {user_id} for org {org_id}")

        # Get member record with profile and role
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

        logger.info(f"[USER_DETAIL] Found user with role: {member_response.data.get('roles', {}).get('name', 'N/A')}")

        # Get user's permissions
        from src.access.permission import permission_service
        permissions = await permission_service.get_user_permissions(user_id, org_id)

        logger.info(f"[USER_DETAIL] User permissions: {len(permissions)} modules")
        for module, actions in list(permissions.items())[:3]:
            logger.info(f"[USER_DETAIL]   {module}: {list(actions.keys())}")

        # Get branch assignments
        branches_response = (
            db.admin.table("user_branches")
            .select("*, branches(id, name, code, branch_type)")
            .eq("user_id", str(user_id))
            .execute()
        )

        result = {
            **member_response.data,
            "user": member_response.data.get("profiles"),
            "role": member_response.data.get("roles"),
            "permissions": permissions,
            "branches": [
                {**b["branches"], "is_primary": b["is_primary"]}
                for b in branches_response.data
            ],
        }

        logger.info(f"[USER_DETAIL] Returning user with {len(result['permissions'])} permission modules, role: {result.get('role', {}).get('name', 'N/A')}")
        return result

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

