"""Auto-split permission service part."""
from typing import Optional, Dict, List
from uuid import UUID
import logging

from src.core.database import db
from src.core.cache import cache

logger = logging.getLogger(__name__)


class PermissionFolderAccessMixin:
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

        # Check if permission already exists
        existing = db.admin.table("node_permissions").select("id").eq(
            "node_id", str(folder_id)
        ).eq("user_id", str(user_id)).maybe_single().execute()

        if existing and existing.data:
            # Update existing permission
            result = db.admin.table("node_permissions").update(
                {
                    "permission": permission,
                    "granted_by": str(granted_by)
                }
            ).eq("id", existing.data["id"]).execute()
        else:
            # Insert new permission
            result = db.admin.table("node_permissions").insert(data).execute()

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
