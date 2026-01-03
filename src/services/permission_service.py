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

    @staticmethod
    async def grant_folder_access(
        user_id: UUID,
        folder_id: UUID,
        org_id: UUID,
        granted_by: UUID,
        permission: str = "view",
    ) -> dict:
        """
        Grant a user access to a specific folder.

        Args:
            user_id: User UUID
            folder_id: Folder/Node UUID
            org_id: Organization UUID
            granted_by: User granting the permission
            permission: Permission level (view, edit, admin)

        Returns:
            The created node_permission record
        """
        data = {
            "node_id": str(folder_id),
            "user_id": str(user_id),
            "permission": permission,
            "granted_by": str(granted_by),
        }

        # Upsert to handle existing permissions
        result = db.admin.table("node_permissions").upsert(
            data,
            on_conflict="node_id,user_id",
        ).execute()

        # Invalidate cache
        await cache.delete_pattern(f"folder_access:{user_id}:*")

        return result.data[0] if result.data else None

    @staticmethod
    async def revoke_folder_access(
        user_id: UUID,
        folder_id: UUID,
    ) -> bool:
        """
        Revoke a user's access to a specific folder.

        Args:
            user_id: User UUID
            folder_id: Folder/Node UUID

        Returns:
            True if successful
        """
        db.admin.table("node_permissions").delete().eq(
            "node_id", str(folder_id)
        ).eq("user_id", str(user_id)).execute()

        # Invalidate cache
        await cache.delete_pattern(f"folder_access:{user_id}:*")

        return True

    @staticmethod
    async def get_user_folder_access(
        user_id: UUID,
        org_id: UUID,
    ) -> List[dict]:
        """
        Get all folders a user has explicit access to.

        Args:
            user_id: User UUID
            org_id: Organization UUID

        Returns:
            List of folder access records with folder details
        """
        result = db.admin.table("node_permissions").select(
            "*, storage_nodes(id, name, node_type, parent_id, branch_id)"
        ).eq("user_id", str(user_id)).execute()

        # Filter to only include folders from this org
        folder_access = []
        for item in result.data:
            node = item.get("storage_nodes")
            if node and node.get("node_type") == "folder":
                folder_access.append({
                    "id": item["id"],
                    "folder_id": item["node_id"],
                    "folder_name": node.get("name"),
                    "branch_id": node.get("branch_id"),
                    "permission": item["permission"],
                    "granted_at": item.get("created_at"),
                })

        return folder_access

    @staticmethod
    async def get_accessible_folder_ids(
        user_id: UUID,
        org_id: UUID,
    ) -> set:
        """
        Get all folder IDs a user can access (including children of accessible folders).

        Args:
            user_id: User UUID
            org_id: Organization UUID

        Returns:
            Set of accessible folder IDs
        """
        # Check cache
        cache_key = f"folder_access:{user_id}:{org_id}"
        cached = await cache.get(cache_key)
        if cached:
            return set(cached)

        # Get explicitly granted folders
        result = db.admin.table("node_permissions").select(
            "node_id"
        ).eq("user_id", str(user_id)).execute()

        granted_folders = {item["node_id"] for item in result.data}

        if not granted_folders:
            await cache.set(cache_key, [], ttl=300)
            return set()

        # Get all child folders recursively
        accessible = set(granted_folders)

        # Fetch all folders in org to build hierarchy
        all_folders = db.admin.table("storage_nodes").select(
            "id, parent_id"
        ).eq("org_id", str(org_id)).eq("node_type", "folder").eq("status", "active").execute()

        # Build parent->children map
        children_map = {}
        for folder in all_folders.data:
            parent = folder.get("parent_id")
            if parent:
                if parent not in children_map:
                    children_map[parent] = []
                children_map[parent].append(folder["id"])

        # BFS to find all accessible children
        queue = list(granted_folders)
        while queue:
            current = queue.pop(0)
            children = children_map.get(current, [])
            for child in children:
                if child not in accessible:
                    accessible.add(child)
                    queue.append(child)

        await cache.set(cache_key, list(accessible), ttl=300)
        return accessible

    @staticmethod
    async def check_folder_access(
        user_id: UUID,
        folder_id: UUID,
        org_id: UUID,
    ) -> bool:
        """
        Check if user has access to a specific folder.

        Args:
            user_id: User UUID
            folder_id: Folder UUID
            org_id: Organization UUID

        Returns:
            True if user has access
        """
        accessible = await PermissionService.get_accessible_folder_ids(user_id, org_id)
        return str(folder_id) in accessible

    @staticmethod
    async def is_admin_or_owner(
        user_id: UUID,
        org_id: UUID,
    ) -> bool:
        """
        Check if user is admin or owner (has full folder access).

        Args:
            user_id: User UUID
            org_id: Organization UUID

        Returns:
            True if user is admin or owner
        """
        member_response = (
            db.admin.table("organization_members")
            .select("roles(slug)")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .eq("status", "active")
            .maybe_single()
            .execute()
        )

        if not member_response or not member_response.data:
            return False

        role_slug = member_response.data.get("roles", {}).get("slug", "")
        return role_slug in ["owner", "admin"]


# Global permission service instance
permission_service = PermissionService()
