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
        logger.info(f"[ROLES] Fetching roles for org_id: {org_id}")

        response = (
            db.admin.table("roles")
            .select("*")
            .eq("org_id", str(org_id))
            .order("priority", desc=True)
            .execute()
        )

        logger.info(f"[ROLES] Found {len(response.data)} roles in database")

        roles = []
        for role in response.data:
            logger.info(f"[ROLES] Processing role: {role['name']} (id: {role['id']})")

            # Get permissions with IDs
            perm_response = (
                db.admin.table("role_permissions")
                .select("permission_id", count="exact")
                .eq("role_id", role["id"])
                .execute()
            )

            logger.info(f"[ROLES] Role '{role['name']}': count={perm_response.count}, data_length={len(perm_response.data or [])}")

            # Get user count
            user_count = (
                db.admin.table("organization_members")
                .select("user_id", count="exact")
                .eq("role_id", role["id"])
                .eq("status", "active")
                .execute()
            )

            # Extract permission IDs for UI
            permission_ids = [p["permission_id"] for p in (perm_response.data or [])]
            logger.info(f"[ROLES] Role '{role['name']}': permission_ids={permission_ids[:5]}... (showing first 5)")

            roles.append({
                **role,
                "permission_count": perm_response.count or 0,
                "permission_ids": permission_ids,  # Include IDs in list view
                "user_count": user_count.count or 0,
            })

        logger.info(f"[ROLES] Returning {len(roles)} roles with permission data")
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
        logger.info(f"[ROLE_DETAIL] Fetching role {role_id} for org {org_id}")

        # Get role
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

        logger.info(f"[ROLE_DETAIL] Found role: {role_response.data['name']}")

        # Get permissions
        perms_response = (
            db.admin.table("role_permissions")
            .select("permission_id, permissions(*, permission_modules(*))")
            .eq("role_id", str(role_id))
            .execute()
        )

        logger.info(f"[ROLE_DETAIL] Permissions query returned {len(perms_response.data or [])} records")

        # Get user count
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
            # Add permission ID
            permission_ids.append(p["permission_id"])

            # Add full permission details if available
            if p.get("permissions"):
                permissions.append({
                    **p["permissions"],
                    "module": p["permissions"].get("permission_modules", {}),
                })

        logger.info(f"[ROLE_DETAIL] Extracted {len(permission_ids)} permission IDs")
        logger.info(f"[ROLE_DETAIL] Permission IDs: {permission_ids[:5]}... (first 5)")

        result = {
            **role_response.data,
            "permissions": permissions,
            "permission_ids": permission_ids,  # Simple array of IDs for UI checkboxes
            "permission_count": len(permissions),
            "user_count": user_count.count or 0,
        }

        logger.info(f"[ROLE_DETAIL] Returning role with {result['permission_count']} permissions")
        return result

