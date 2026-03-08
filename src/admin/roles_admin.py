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


class AdminRolesAdminMixin:
    async def delete_role(
        org_id: UUID,
        role_id: UUID,
        deleted_by: UUID,
    ) -> bool:
        """
        Delete a custom role.

        Args:
            org_id: Organization UUID
            role_id: Role UUID
            deleted_by: User deleting

        Returns:
            True if successful
        """
        # Verify role exists
        existing = (
            db.admin.table("roles")
            .select("name, slug, is_system_role")
            .eq("id", str(role_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Role not found")

        # Only Owner role is protected from deletion
        if existing.data["slug"] == "owner":
            raise AuthorizationError("Cannot delete the Owner role")

        # Check if role has users
        user_count = (
            db.admin.table("organization_members")
            .select("user_id", count="exact")
            .eq("role_id", str(role_id))
            .eq("status", "active")
            .execute()
        )

        if user_count.count and user_count.count > 0:
            raise ConflictError(f"Cannot delete role - {user_count.count} users still assigned")

        # Delete role (cascade deletes role_permissions)
        db.admin.table("roles").delete().eq("id", str(role_id)).execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=deleted_by,
            action=AuditAction.DELETE,
            resource_type="role",
            resource_id=role_id,
            resource_name=existing.data["name"],
            description=f"Deleted role: {existing.data['name']}",
        )

        return True

    @staticmethod
    async def get_all_permissions() -> List[dict]:
        """
        Get all available permissions grouped by module.

        Returns:
            List of permission modules with their permissions
        """
        # Get all modules
        modules_response = (
            db.admin.table("permission_modules")
            .select("*")
            .eq("is_active", True)
            .order("sort_order")
            .execute()
        )

        # Get all permissions
        perms_response = (
            db.admin.table("permissions")
            .select("*")
            .order("sort_order")
            .execute()
        )

        # Group permissions by module
        permissions_by_module = {}
        for perm in perms_response.data:
            module_id = perm["module_id"]
            if module_id not in permissions_by_module:
                permissions_by_module[module_id] = []
            permissions_by_module[module_id].append(perm)

        # Build response
        modules = []
        for module in modules_response.data:
            modules.append({
                **module,
                "permissions": permissions_by_module.get(module["id"], []),
            })

        return modules


