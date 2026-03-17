"""Group list/get/create/update operations."""
from typing import List
from uuid import UUID

from src.core.database import db
from src.core.exceptions import NotFoundError, ConflictError
from src.audit.service import audit_service
from src.models.audit import AuditAction


class GroupReadWriteMixin:
    @staticmethod
    async def list_groups(org_id: UUID) -> List[dict]:
        """
        List all groups for an organization.

        Args:
            org_id: Organization UUID

        Returns:
            List of groups with member counts
        """
        response = (
            db.admin.table("groups")
            .select("*")
            .eq("org_id", str(org_id))
            .order("name")
            .execute()
        )

        groups = []
        for group in response.data:
            member_count = (
                db.admin.table("group_members")
                .select("user_id", count="exact")
                .eq("group_id", group["id"])
                .execute()
            )
            groups.append({
                **group,
                "member_count": member_count.count or 0,
            })

        return groups

    @staticmethod
    async def get_group(org_id: UUID, group_id: UUID) -> dict:
        """
        Get group details with members.

        Args:
            org_id: Organization UUID
            group_id: Group UUID

        Returns:
            Group details with member list
        """
        group = (
            db.admin.table("groups")
            .select("*")
            .eq("id", str(group_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not group.data:
            raise NotFoundError("Group not found")

        members = (
            db.admin.table("group_members")
            .select("*, profiles(id, full_name, email, avatar_url)")
            .eq("group_id", str(group_id))
            .execute()
        )

        return {
            **group.data,
            "members": [
                {
                    **m,
                    "user": m.get("profiles"),
                }
                for m in members.data
            ],
            "member_count": len(members.data),
        }

    @staticmethod
    async def create_group(
        org_id: UUID,
        data: dict,
        created_by: UUID,
    ) -> dict:
        """
        Create a new group.

        Args:
            org_id: Organization UUID
            data: Group data
            created_by: User creating the group

        Returns:
            Created group
        """
        # Generate slug
        slug = data["name"].lower().replace(" ", "-")

        # Check for duplicate
        existing = (
            db.admin.table("groups")
            .select("id")
            .eq("org_id", str(org_id))
            .eq("slug", slug)
            .maybe_single()
            .execute()
        )

        if existing and existing.data:
            raise ConflictError(f"Group '{data['name']}' already exists")

        # Create group
        group_data = {
            "org_id": str(org_id),
            "name": data["name"],
            "slug": slug,
            "description": data.get("description"),
            "color": data.get("color", "#6366f1"),
            "icon": data.get("icon"),
            "group_type": data.get("group_type", "custom"),
            "created_by": str(created_by),
        }

        response = db.admin.table("groups").insert(group_data).execute()

        if not response.data:
            raise Exception("Failed to create group")

        group = response.data[0]

        # Add initial members if provided
        member_ids = data.get("member_ids", [])
        if member_ids:
            members_data = [
                {
                    "group_id": group["id"],
                    "user_id": str(uid),
                    "added_by": str(created_by),
                }
                for uid in member_ids
            ]
            db.admin.table("group_members").insert(members_data).execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=created_by,
            action=AuditAction.CREATE,
            resource_type="group",
            resource_id=UUID(group["id"]),
            resource_name=group["name"],
            description=f"Created group: {group['name']}",
        )

        return {**group, "member_count": len(member_ids)}

    @staticmethod
    async def update_group(
        org_id: UUID,
        group_id: UUID,
        data: dict,
        updated_by: UUID,
    ) -> dict:
        """
        Update a group.

        Args:
            org_id: Organization UUID
            group_id: Group UUID
            data: Update data
            updated_by: User updating

        Returns:
            Updated group
        """
        # Verify group exists
        existing = (
            db.admin.table("groups")
            .select("*")
            .eq("id", str(group_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Group not found")

        update_data = {}
        if data.get("name"):
            update_data["name"] = data["name"]
            update_data["slug"] = data["name"].lower().replace(" ", "-")
        
        for field in ["description", "color", "icon", "group_type"]:
            if data.get(field) is not None:
                update_data[field] = data[field]

        if update_data:
            response = (
                db.admin.table("groups")
                .update(update_data)
                .eq("id", str(group_id))
                .execute()
            )
            
            # Log audit
            await audit_service.log(
                org_id=org_id,
                user_id=updated_by,
                action=AuditAction.UPDATE,
                resource_type="group",
                resource_id=group_id,
                resource_name=data.get("name", existing.data["name"]),
                description=f"Updated group: {data.get('name', existing.data['name'])}",
            )
            
            return response.data[0]

        return existing.data
