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


class AdminRolesWriteMixin:
    async def create_role(
        org_id: UUID,
        data: dict,
        created_by: UUID,
    ) -> dict:
        """
        Create a custom role.

        Args:
            org_id: Organization UUID
            data: Role data with permission_ids
            created_by: User creating the role

        Returns:
            Created role
        """
        # Generate slug
        slug = data["name"].lower().replace(" ", "-")

        # Check for duplicate slug
        existing = (
            db.admin.table("roles")
            .select("id")
            .eq("org_id", str(org_id))
            .eq("slug", slug)
            .maybe_single()
            .execute()
        )

        if existing and existing.data:
            raise ConflictError(f"Role with name '{data['name']}' already exists")

        # Create role
        role_data = {
            "org_id": str(org_id),
            "name": data["name"],
            "slug": slug,
            "description": data.get("description"),
            "color": data.get("color", "#6366f1"),
            "icon": data.get("icon"),
            "is_system_role": False,
            "is_custom_role": True,
            "priority": 500,  # Middle priority for custom roles
            "created_by": str(created_by),
        }

        response = db.admin.table("roles").insert(role_data).execute()

        if not response.data:
            raise Exception("Failed to create role")

        role = response.data[0]

        # Assign permissions
        permission_ids = data.get("permission_ids", [])
        if permission_ids:
            role_perms = [
                {
                    "role_id": role["id"],
                    "permission_id": str(pid),
                    "granted_by": str(created_by),
                }
                for pid in permission_ids
            ]
            db.admin.table("role_permissions").insert(role_perms).execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=created_by,
            action=AuditAction.CREATE,
            resource_type="role",
            resource_id=UUID(role["id"]),
            resource_name=role["name"],
            description=f"Created role: {role['name']}",
        )

        return {**role, "permission_count": len(permission_ids)}

    @staticmethod
    async def update_role(
        org_id: UUID,
        role_id: UUID,
        data: dict,
        updated_by: UUID,
    ) -> dict:
        """
        Update a role.

        Args:
            org_id: Organization UUID
            role_id: Role UUID
            data: Update data
            updated_by: User updating

        Returns:
            Updated role
        """
        # Verify role exists
        existing = (
            db.admin.table("roles")
            .select("*")
            .eq("id", str(role_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Role not found")

        # Only Owner role is protected from modification
        if existing.data["slug"] == "owner":
            raise AuthorizationError("Cannot modify the Owner role")

        # Update role fields
        update_data = {}
        if data.get("name"):
            update_data["name"] = data["name"]
            # Generate slug and ensure it's unique
            base_slug = data["name"].lower().replace(" ", "-")
            # Only check for uniqueness if the slug is different from current
            if base_slug != existing.data.get("slug"):
                # Check if slug exists for another role
                existing_slug = (
                    db.admin.table("roles")
                    .select("id")
                    .eq("org_id", str(org_id))
                    .eq("slug", base_slug)
                    .neq("id", str(role_id))
                    .maybe_single()
                    .execute()
                )
                # If slug exists, append a number
                if existing_slug and existing_slug.data:
                    counter = 1
                    while True:
                        new_slug = f"{base_slug}-{counter}"
                        check_slug = (
                            db.admin.table("roles")
                            .select("id")
                            .eq("org_id", str(org_id))
                            .eq("slug", new_slug)
                            .maybe_single()
                            .execute()
                        )
                        if not check_slug or not check_slug.data:
                            update_data["slug"] = new_slug
                            break
                        counter += 1
                else:
                    update_data["slug"] = base_slug
            # else: keep the existing slug if name generates the same slug
        if data.get("description") is not None:
            update_data["description"] = data["description"]
        if data.get("color"):
            update_data["color"] = data["color"]
        if data.get("icon") is not None:
            update_data["icon"] = data["icon"]

        if update_data:
            update_data["updated_at"] = "now()"
            db.admin.table("roles").update(update_data).eq("id", str(role_id)).execute()

        # Update permissions if provided
        if data.get("permission_ids") is not None:
            # Remove existing permissions
            db.admin.table("role_permissions").delete().eq("role_id", str(role_id)).execute()

            # Add new permissions
            permission_ids = data["permission_ids"]
            if permission_ids:
                role_perms = [
                    {
                        "role_id": str(role_id),
                        "permission_id": str(pid),
                        "granted_by": str(updated_by),
                    }
                    for pid in permission_ids
                ]
                db.admin.table("role_permissions").insert(role_perms).execute()

            # Update the role's updated_at timestamp even if only permissions changed
            db.admin.table("roles").update({"updated_at": "now()"}).eq("id", str(role_id)).execute()

            # Invalidate permission cache for all users with this role
            members = (
                db.admin.table("organization_members")
                .select("user_id")
                .eq("role_id", str(role_id))
                .execute()
            )
            for member in members.data:
                await cache.delete_pattern(f"permission:{member['user_id']}:*")
                await cache.delete_pattern(f"user_permissions:{member['user_id']}:*")

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=updated_by,
            action=AuditAction.UPDATE,
            resource_type="role",
            resource_id=role_id,
            resource_name=data.get("name", existing.data["name"]),
            description=f"Updated role: {data.get('name', existing.data['name'])}",
        )

        # Return updated role
        return await AdminService.get_role_with_permissions(org_id, role_id)

