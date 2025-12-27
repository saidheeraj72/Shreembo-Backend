"""
Permission service for RBAC and permission checks.
"""
from typing import Dict, List, Optional
from uuid import UUID
import logging

from src.core.database import db
from src.core.cache import cache
from src.core.exceptions import NotFoundError, AuthorizationError

logger = logging.getLogger(__name__)


class PermissionService:
    """Service for managing permissions and roles."""

    @staticmethod
    async def check_permission(
        user_id: UUID,
        org_id: UUID,
        module: str,
        action: str,
    ) -> bool:
        """
        Check if user has a specific permission.

        Permission resolution order:
        1. Check if user is Owner (Owner has all permissions)
        2. Check user-level permission overrides (user_permissions)
        3. Check role-based permissions (via organization_members -> roles -> role_permissions)
        4. Default deny if not found

        Args:
            user_id: User UUID
            org_id: Organization UUID
            module: Permission module key (e.g., 'documents')
            action: Permission action (e.g., 'view')

        Returns:
            True if user has permission, False otherwise
        """
        # Check cache first
        cache_key = f"permission:{user_id}:{org_id}:{module}:{action}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

        # Get user's membership and role info
        member_response = (
            db.admin.table("organization_members")
            .select("role_id, roles(slug, is_system_role)")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .eq("status", "active")
            .maybe_single()
            .execute()
        )

        if not member_response or not member_response.data or not member_response.data.get("role_id"):
            # User not in org or no role assigned
            await cache.set(cache_key, False, ttl=60)
            return False

        role_id = member_response.data["role_id"]
        role_info = member_response.data.get("roles", {})

        # 1. Owner role has ALL permissions by default
        if role_info and role_info.get("slug") == "owner":
            await cache.set(cache_key, True)
            return True

        # Get permission ID
        permission_response = (
            db.admin.table("permissions")
            .select("id, permission_modules!inner(key)")
            .eq("permission_modules.key", module)
            .eq("action", action)
            .single()
            .execute()
        )

        if not permission_response.data:
            # Permission doesn't exist
            await cache.set(cache_key, False, ttl=60)
            return False

        permission_id = permission_response.data["id"]

        # 2. Check user-level permission override
        user_perm_response = (
            db.admin.table("user_permissions")
            .select("is_granted")
            .eq("user_id", str(user_id))
            .eq("permission_id", str(permission_id))
            .maybe_single()
            .execute()
        )

        if user_perm_response and user_perm_response.data:
            result = user_perm_response.data["is_granted"]
            await cache.set(cache_key, result)
            return result

        # 3. Check role-based permissions
        role_perm_response = (
            db.admin.table("role_permissions")
            .select("permission_id")
            .eq("role_id", str(role_id))
            .eq("permission_id", str(permission_id))
            .maybe_single()
            .execute()
        )

        result = role_perm_response is not None and role_perm_response.data is not None
        await cache.set(cache_key, result)
        return result

    @staticmethod
    async def get_user_permissions(
        user_id: UUID,
        org_id: UUID,
    ) -> Dict[str, Dict[str, bool]]:
        """
        Get all permissions for a user in an organization.

        Returns permissions grouped by module:
        {
            "documents": {"view": True, "create": True, "edit": False, ...},
            "users": {"view": True, "invite": False, ...},
            ...
        }

        Args:
            user_id: User UUID
            org_id: Organization UUID

        Returns:
            Dictionary of permissions by module and action
        """
        logger.info(f"[PERMS] Getting permissions for user {user_id} in org {org_id}")

        # Check cache
        cache_key = f"user_permissions:{user_id}:{org_id}"
        cached = await cache.get(cache_key)
        if cached:
            logger.info(f"[PERMS] Returning cached permissions: {len(cached)} modules")
            return cached

        # Get user's role with role info
        member_response = (
            db.admin.table("organization_members")
            .select("role_id, roles(slug, is_system_role)")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .eq("status", "active")
            .maybe_single()
            .execute()
        )

        if not member_response or not member_response.data or not member_response.data.get("role_id"):
            logger.info(f"[PERMS] User not found in org or no role assigned")
            return {}

        role_id = member_response.data["role_id"]
        role_info = member_response.data.get("roles", {})

        logger.info(f"[PERMS] User has role_id: {role_id}, slug: {role_info.get('slug', 'N/A')}")

        # Owner role has ALL permissions
        if role_info and role_info.get("slug") == "owner":
            logger.info(f"[PERMS] User is Owner - granting all permissions")
            # Get all permissions and grant them all
            all_perms_response = (
                db.admin.table("permissions")
                .select("*, permission_modules(key)")
                .execute()
            )
            permissions: Dict[str, Dict[str, bool]] = {}
            for perm in all_perms_response.data:
                module_key = perm["permission_modules"]["key"]
                action = perm["action"]
                if module_key not in permissions:
                    permissions[module_key] = {}
                permissions[module_key][action] = True
            logger.info(f"[PERMS] Owner granted {len(permissions)} modules")
            await cache.set(cache_key, permissions)
            return permissions

        # Get all permissions from role
        role_perms_response = (
            db.admin.table("role_permissions")
            .select("permissions(*, permission_modules(key))")
            .eq("role_id", str(role_id))
            .execute()
        )

        logger.info(f"[PERMS] Role permissions query returned {len(role_perms_response.data)} records")

        # Build permissions dict
        permissions: Dict[str, Dict[str, bool]] = {}
        for item in role_perms_response.data:
            perm = item["permissions"]
            module_key = perm["permission_modules"]["key"]
            action = perm["action"]

            if module_key not in permissions:
                permissions[module_key] = {}
            permissions[module_key][action] = True

        logger.info(f"[PERMS] Built {len(permissions)} permission modules from role")

        # Apply user-level overrides
        user_perms_response = (
            db.admin.table("user_permissions")
            .select("is_granted, permissions(*, permission_modules(key))")
            .eq("user_id", str(user_id))
            .execute()
        )

        logger.info(f"[PERMS] User-level overrides: {len(user_perms_response.data)} records")

        for item in user_perms_response.data:
            perm = item["permissions"]
            module_key = perm["permission_modules"]["key"]
            action = perm["action"]
            is_granted = item["is_granted"]

            if module_key not in permissions:
                permissions[module_key] = {}
            permissions[module_key][action] = is_granted

        # Cache result
        await cache.set(cache_key, permissions)
        logger.info(f"[PERMS] Returning {len(permissions)} permission modules")
        return permissions

    @staticmethod
    async def grant_user_permission(
        user_id: UUID,
        permission_id: UUID,
        granted_by: UUID,
    ) -> bool:
        """
        Grant a permission to a user (override).

        Args:
            user_id: User UUID
            permission_id: Permission UUID
            granted_by: User granting the permission

        Returns:
            True if successful
        """
        db.admin.table("user_permissions").upsert(
            {
                "user_id": str(user_id),
                "permission_id": str(permission_id),
                "is_granted": True,
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


# Global permission service instance
permission_service = PermissionService()
