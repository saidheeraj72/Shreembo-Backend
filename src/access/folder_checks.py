"""Auto-split permission service part."""
from typing import Optional, Dict, List
from uuid import UUID
from datetime import datetime, timezone
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

        Resolution order:
        1. Org admin/owner -> full access.
        2. Walk up the folder tree. At each node, consider explicit grants for
           the user directly OR for any group the user belongs to, ignoring
           expired grants. Any grant that meets the required level immediately
           allows access.
        3. Otherwise fall back to the org RBAC capability (documents.view /
           documents.edit). Folder grants are ADDITIVE ONLY: they can raise a
           user's access (e.g. share a folder with someone whose role would not
           otherwise reach it) but never drop a member below the document
           capability their role already grants. So an explicit view grant does
           not block a member who holds documents.edit from uploading.
           ('admin' level is never granted via this fallback.)
        """
        # 1. Check admin/owner
        if await PermissionService.is_admin_or_owner(user_id, org_id):
            return True

        # share_permission enum: view < comment < edit < admin
        levels = {"view": 1, "comment": 1, "edit": 2, "admin": 3}
        req_val = levels.get(required_level, 1)

        # Groups this user belongs to (for group-based folder grants)
        group_rows = (
            db.admin.table("group_members")
            .select("group_id")
            .eq("user_id", str(user_id))
            .execute()
        )
        group_ids = {row["group_id"] for row in (group_rows.data or [])}

        now = datetime.now(timezone.utc)

        def _not_expired(row: dict) -> bool:
            expires_at = row.get("expires_at")
            if not expires_at:
                return True
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                # Unparseable expiry -> treat as non-expiring rather than lock out
                return True
            return expiry > now

        # 2. Walk up the tree looking for the nearest applicable grant
        current_id = str(folder_id)
        while current_id:
            perms = (
                db.admin.table("node_permissions")
                .select("permission, user_id, group_id, expires_at")
                .eq("node_id", current_id)
                .execute()
            )

            applicable = [
                row for row in (perms.data or [])
                if _not_expired(row)
                and (
                    row.get("user_id") == str(user_id)
                    or (row.get("group_id") and row.get("group_id") in group_ids)
                )
            ]

            if applicable:
                best = max(levels.get(row["permission"], 0) for row in applicable)
                if best >= req_val:
                    return True
                # Explicit grant is below the required level: it does not deny.
                # Keep walking up in case an ancestor grants more, then fall
                # back to the org capability below.

            # Move to parent
            node = (
                db.admin.table("storage_nodes")
                .select("parent_id")
                .eq("id", current_id)
                .maybe_single()
                .execute()
            )

            if not node or not node.data:
                break

            current_id = node.data.get("parent_id")

        # 3. No explicit folder grant. 'admin' is only via org admin/owner or an
        # explicit grant; view/edit fall back to the org document capability.
        if req_val >= levels["admin"]:
            return False

        return await PermissionService.check_permission(
            user_id=user_id,
            org_id=org_id,
            module="documents",
            action="edit" if req_val >= levels["edit"] else "view",
        )

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

        role = member_response.data.get("roles") or {}
        role_slug = role.get("slug", "")
        return role_slug in ["owner", "admin"]
