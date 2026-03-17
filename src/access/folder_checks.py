"""Auto-split permission service part."""
from typing import Optional, Dict, List
from uuid import UUID
import logging

from src.core.database import db
from src.core.cache import cache

logger = logging.getLogger(__name__)


class PermissionFolderChecksMixin:
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
    async def check_folder_permission(
        user_id: UUID,
        folder_id: UUID,
        org_id: UUID,
        required_level: str = "view",
    ) -> bool:
        """
        Check if user has specific permission level on a folder.
        Levels: view < edit < admin
        """
        # 1. Check admin/owner
        if await PermissionService.is_admin_or_owner(user_id, org_id):
            return True

        # Define levels
        levels = {"view": 1, "edit": 2, "admin": 3}
        req_val = levels.get(required_level, 1)

        # 2. Check permissions walking up the tree
        # We need to find the nearest permission grant
        
        current_id = str(folder_id)
        while current_id:
            # Check direct permission
            perm = db.admin.table("node_permissions").select("permission").eq(
                "node_id", current_id
            ).eq("user_id", str(user_id)).maybe_single().execute()

            if perm and perm.data:
                user_level = perm.data["permission"]
                return levels.get(user_level, 0) >= req_val
            
            # Move to parent
            node = db.admin.table("storage_nodes").select("parent_id").eq(
                "id", current_id
            ).single().execute()
            
            if not node or not node.data:
                break
                
            current_id = node.data.get("parent_id")
            
        return False

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
