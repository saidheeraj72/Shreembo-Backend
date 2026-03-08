"""Auto-split permission service part."""
from typing import Optional, Dict, List
from uuid import UUID
import logging

from src.core.database import db
from src.core.cache import cache

logger = logging.getLogger(__name__)


class PermissionOverridesMixin:
    async def grant_user_permission(
        user_id: UUID,
        permission_id: UUID,
        granted_by: UUID,
        is_granted: bool = True,
    ) -> bool:
        """
        Grant or deny a permission to a user (override).

        Args:
            user_id: User UUID
            permission_id: Permission UUID
            granted_by: User granting the permission
            is_granted: True to grant, False to deny

        Returns:
            True if successful
        """
        db.admin.table("user_permissions").upsert(
            {
                "user_id": str(user_id),
                "permission_id": str(permission_id),
                "is_granted": is_granted,
                "granted_by": str(granted_by),
            },
            on_conflict="user_id,permission_id",
        ).execute()

        # Invalidate cache
        await cache.delete_pattern(f"permission:{user_id}:*")
        await cache.delete_pattern(f"user_permissions:{user_id}:*")

        return True

    @staticmethod
    async def revoke_user_permission(
        user_id: UUID,
        permission_id: UUID,
    ) -> bool:
        """
        Revoke a user permission override.

        Args:
            user_id: User UUID
            permission_id: Permission UUID

        Returns:
            True if successful
        """
        db.admin.table("user_permissions").delete().eq(
            "user_id", str(user_id)
        ).eq("permission_id", str(permission_id)).execute()

        # Invalidate cache
        await cache.delete_pattern(f"permission:{user_id}:*")
        await cache.delete_pattern(f"user_permissions:{user_id}:*")

        return True

    @staticmethod
    async def get_all_modules() -> List[dict]:
        """
        Get all permission modules.

        Returns:
            List of permission modules
        """
        response = (
            db.admin.table("permission_modules")
            .select("*")
            .order("sort_order")
            .execute()
        )
        return response.data

    @staticmethod
    async def get_module_permissions(module_key: str) -> List[dict]:
        """
        Get all permissions for a module.

        Args:
            module_key: Module key (e.g., 'documents')

        Returns:
            List of permissions
        """
        response = (
            db.admin.table("permissions")
            .select("*, permission_modules!inner(key)")
            .eq("permission_modules.key", module_key)
            .order("sort_order")
            .execute()
        )
        return response.data


    # ==========================================
    # FOLDER ACCESS PERMISSIONS
    # ==========================================

