"""
Admin service for organization-level management of branches, users, roles, and invitations.
"""
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from uuid import UUID
import secrets
import logging

from src.core.database import db
from src.core.cache import cache
from src.core.exceptions import NotFoundError, ConflictError, AuthorizationError
from src.services.audit_service import audit_service
from src.services.email_service import email_service
from src.models.audit import AuditAction

logger = logging.getLogger(__name__)


class AdminService:
    """Service for organization admin operations."""

    # ==========================================
    # BRANCH MANAGEMENT
    # ==========================================

    @staticmethod
    async def list_branches(
        org_id: UUID,
        include_inactive: bool = False,
    ) -> List[dict]:
        """
        List all branches for an organization.

        Args:
            org_id: Organization UUID
            include_inactive: Whether to include inactive branches

        Returns:
            List of branches with manager details and user counts
        """
        query = (
            db.admin.table("branches")
            .select("*, profiles!fk_branch_manager(id, full_name, email, avatar_url)")
            .eq("org_id", str(org_id))
            .order("name")
        )

        if not include_inactive:
            query = query.eq("is_active", True)

        response = query.execute()

        # Get user counts for each branch
        branches = []
        for branch in response.data:
            user_count_response = (
                db.admin.table("user_branches")
                .select("user_id", count="exact")
                .eq("branch_id", branch["id"])
                .execute()
            )

            branches.append({
                **branch,
                "manager": branch.get("profiles"),
                "user_count": user_count_response.count or 0,
            })

        return branches

    @staticmethod
    async def get_branch(org_id: UUID, branch_id: UUID) -> dict:
        """
        Get a single branch with details.

        Args:
            org_id: Organization UUID
            branch_id: Branch UUID

        Returns:
            Branch details with manager and assigned users

        Raises:
            NotFoundError: If branch not found
        """
        response = (
            db.admin.table("branches")
            .select("*, profiles!fk_branch_manager(id, full_name, email, avatar_url)")
            .eq("id", str(branch_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not response.data:
            raise NotFoundError("Branch not found")

        # Get assigned users
        users_response = (
            db.admin.table("user_branches")
            .select("*, profiles(id, full_name, email, avatar_url, status)")
            .eq("branch_id", str(branch_id))
            .execute()
        )

        return {
            **response.data,
            "manager": response.data.get("profiles"),
            "users": [
                {
                    **u["profiles"],
                    "is_primary": u["is_primary"],
                    "assigned_at": u["assigned_at"],
                }
                for u in users_response.data
            ],
            "user_count": len(users_response.data),
        }

    @staticmethod
    async def create_branch(
        org_id: UUID,
        data: dict,
        created_by: UUID,
    ) -> dict:
        """
        Create a new branch.

        Args:
            org_id: Organization UUID
            data: Branch data
            created_by: User creating the branch

        Returns:
            Created branch

        Raises:
            ConflictError: If branch code already exists
        """
        # Check for duplicate code
        if data.get("code"):
            existing = (
                db.admin.table("branches")
                .select("id")
                .eq("org_id", str(org_id))
                .eq("code", data["code"])
                .maybe_single()
                .execute()
            )
            if existing and existing.data:
                raise ConflictError(f"Branch with code '{data['code']}' already exists")

        # Create branch
        branch_data = {
            "org_id": str(org_id),
            **{k: str(v) if isinstance(v, UUID) else v for k, v in data.items()},
        }

        response = db.admin.table("branches").insert(branch_data).execute()

        if not response.data:
            raise Exception("Failed to create branch")

        branch = response.data[0]

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=created_by,
            action=AuditAction.CREATE,
            resource_type="branch",
            resource_id=UUID(branch["id"]),
            resource_name=branch["name"],
            description=f"Created branch: {branch['name']}",
        )

        return branch

    @staticmethod
    async def update_branch(
        org_id: UUID,
        branch_id: UUID,
        data: dict,
        updated_by: UUID,
    ) -> dict:
        """
        Update a branch.

        Args:
            org_id: Organization UUID
            branch_id: Branch UUID
            data: Update data
            updated_by: User updating the branch

        Returns:
            Updated branch

        Raises:
            NotFoundError: If branch not found
        """
        # Verify branch exists and belongs to org
        existing = (
            db.admin.table("branches")
            .select("*")
            .eq("id", str(branch_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Branch not found")

        # Check for duplicate code if code is being updated
        if data.get("code") and data["code"] != existing.data.get("code"):
            code_check = (
                db.admin.table("branches")
                .select("id")
                .eq("org_id", str(org_id))
                .eq("code", data["code"])
                .neq("id", str(branch_id))
                .maybe_single()
                .execute()
            )
            if code_check and code_check.data:
                raise ConflictError(f"Branch with code '{data['code']}' already exists")

        # Update branch
        update_data = {k: str(v) if isinstance(v, UUID) else v for k, v in data.items() if v is not None}

        response = (
            db.admin.table("branches")
            .update(update_data)
            .eq("id", str(branch_id))
            .execute()
        )

        if not response.data:
            raise Exception("Failed to update branch")

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=updated_by,
            action=AuditAction.UPDATE,
            resource_type="branch",
            resource_id=branch_id,
            resource_name=response.data[0]["name"],
            description=f"Updated branch: {response.data[0]['name']}",
            changes={"before": existing.data, "after": response.data[0]},
        )

        return response.data[0]

    @staticmethod
    async def delete_branch(
        org_id: UUID,
        branch_id: UUID,
        deleted_by: UUID,
    ) -> bool:
        """
        Soft delete a branch (set is_active=false).

        Args:
            org_id: Organization UUID
            branch_id: Branch UUID
            deleted_by: User deleting the branch

        Returns:
            True if successful

        Raises:
            NotFoundError: If branch not found
        """
        # Verify branch exists
        existing = (
            db.admin.table("branches")
            .select("name")
            .eq("id", str(branch_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Branch not found")

        # Soft delete
        db.admin.table("branches").update({
            "is_active": False,
        }).eq("id", str(branch_id)).execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=deleted_by,
            action=AuditAction.DELETE,
            resource_type="branch",
            resource_id=branch_id,
            resource_name=existing.data["name"],
            description=f"Deleted branch: {existing.data['name']}",
        )

        return True

    @staticmethod
    async def assign_user_to_branch(
        org_id: UUID,
        branch_id: UUID,
        user_id: UUID,
        assigned_by: UUID,
        is_primary: bool = False,
    ) -> dict:
        """
        Assign a user to a branch.

        Args:
            org_id: Organization UUID
            branch_id: Branch UUID
            user_id: User to assign
            assigned_by: User making the assignment
            is_primary: Whether this is the user's primary branch

        Returns:
            Assignment record
        """
        # Verify branch belongs to org
        branch = (
            db.admin.table("branches")
            .select("name")
            .eq("id", str(branch_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not branch.data:
            raise NotFoundError("Branch not found")

        # Verify user belongs to org
        member = (
            db.admin.table("organization_members")
            .select("id")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .eq("status", "active")
            .maybe_single()
            .execute()
        )

        if not member or not member.data:
            raise NotFoundError("User is not a member of this organization")

        # If setting as primary, remove primary from other branches
        if is_primary:
            db.admin.table("user_branches").update({
                "is_primary": False,
            }).eq("user_id", str(user_id)).execute()

            # Also update profile's primary_branch_id
            db.admin.table("profiles").update({
                "primary_branch_id": str(branch_id),
            }).eq("id", str(user_id)).execute()

        # Upsert assignment
        response = (
            db.admin.table("user_branches")
            .upsert({
                "user_id": str(user_id),
                "branch_id": str(branch_id),
                "is_primary": is_primary,
                "assigned_by": str(assigned_by),
                "assigned_at": datetime.utcnow().isoformat(),
            }, on_conflict="user_id,branch_id")
            .execute()
        )

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=assigned_by,
            action=AuditAction.UPDATE,
            resource_type="branch",
            resource_id=branch_id,
            resource_name=branch.data["name"],
            description=f"Assigned user to branch: {branch.data['name']}",
        )

        return response.data[0] if response.data else {}

    @staticmethod
    async def remove_user_from_branch(
        org_id: UUID,
        branch_id: UUID,
        user_id: UUID,
        removed_by: UUID,
    ) -> bool:
        """
        Remove a user from a branch.

        Args:
            org_id: Organization UUID
            branch_id: Branch UUID
            user_id: User to remove
            removed_by: User making the removal

        Returns:
            True if successful
        """
        # Verify branch belongs to org
        branch = (
            db.admin.table("branches")
            .select("name")
            .eq("id", str(branch_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not branch.data:
            raise NotFoundError("Branch not found")

        # Delete assignment
        db.admin.table("user_branches").delete().eq(
            "user_id", str(user_id)
        ).eq("branch_id", str(branch_id)).execute()

        # If this was primary, clear from profile
        db.admin.table("profiles").update({
            "primary_branch_id": None,
        }).eq("id", str(user_id)).eq("primary_branch_id", str(branch_id)).execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=removed_by,
            action=AuditAction.UPDATE,
            resource_type="branch",
            resource_id=branch_id,
            resource_name=branch.data["name"],
            description=f"Removed user from branch: {branch.data['name']}",
        )

        return True

    # ==========================================
    # USER MANAGEMENT
    # ==========================================

    @staticmethod
    async def list_org_users(
        org_id: UUID,
        status: Optional[str] = None,
    ) -> List[dict]:
        """
        List all organization members with their roles.

        Args:
            org_id: Organization UUID
            status: Optional filter by member status

        Returns:
            List of members with user profiles and roles
        """
        logger.info(f"[USERS] Fetching users for org_id: {org_id}, status filter: {status}")

        query = (
            db.admin.table("organization_members")
            .select("*, profiles!organization_members_user_id_fkey(id, email, full_name, display_name, avatar_url, status, last_active_at), roles(id, name, slug, color, icon)")
            .eq("org_id", str(org_id))
            .order("joined_at", desc=True)
        )

        if status:
            query = query.eq("status", status)

        response = query.execute()
        logger.info(f"[USERS] Found {len(response.data)} members")

        # Get branch assignments for each user
        users = []
        for member in response.data:
            logger.info(f"[USERS] Processing user: {member.get('user_id')}, role: {member.get('roles', {}).get('name', 'N/A')}")

            branches_response = (
                db.admin.table("user_branches")
                .select("*, branches(id, name, code)")
                .eq("user_id", member["user_id"])
                .execute()
            )

            # Enrich role data with permission_ids
            role_data = member.get("roles")
            if role_data and member.get("role_id"):
                # Get permission IDs for this role
                role_perms = (
                    db.admin.table("role_permissions")
                    .select("permission_id")
                    .eq("role_id", member["role_id"])
                    .execute()
                )
                permission_ids = [p["permission_id"] for p in (role_perms.data or [])]
                role_data = {
                    **role_data,
                    "permission_ids": permission_ids,
                    "permission_count": len(permission_ids),
                }
                logger.info(f"[USERS] User {member.get('user_id')}: enriched role with {len(permission_ids)} permissions")

            user_data = {
                **member,
                "user": member.get("profiles"),
                "role": role_data,
                "branches": [
                    {**b["branches"], "is_primary": b["is_primary"]}
                    for b in branches_response.data
                ],
            }

            logger.info(f"[USERS] User {member.get('user_id')}: role_id={member.get('role_id')}, role_name={user_data.get('role', {}).get('name', 'N/A')}")

            users.append(user_data)

        logger.info(f"[USERS] Returning {len(users)} users")
        return users

    @staticmethod
    async def get_user_details(org_id: UUID, user_id: UUID) -> dict:
        """
        Get detailed user information within an organization.

        Args:
            org_id: Organization UUID
            user_id: User UUID

        Returns:
            User details with role, permissions, and branches

        Raises:
            NotFoundError: If user not found in org
        """
        logger.info(f"[USER_DETAIL] Fetching user {user_id} for org {org_id}")

        # Get member record with profile and role
        member_response = (
            db.admin.table("organization_members")
            .select("*, profiles!organization_members_user_id_fkey(*), roles(*)")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .single()
            .execute()
        )

        if not member_response.data:
            raise NotFoundError("User not found in organization")

        logger.info(f"[USER_DETAIL] Found user with role: {member_response.data.get('roles', {}).get('name', 'N/A')}")

        # Get user's permissions
        from src.services.permission_service import permission_service
        permissions = await permission_service.get_user_permissions(user_id, org_id)

        logger.info(f"[USER_DETAIL] User permissions: {len(permissions)} modules")
        for module, actions in list(permissions.items())[:3]:
            logger.info(f"[USER_DETAIL]   {module}: {list(actions.keys())}")

        # Get branch assignments
        branches_response = (
            db.admin.table("user_branches")
            .select("*, branches(id, name, code, branch_type)")
            .eq("user_id", str(user_id))
            .execute()
        )

        result = {
            **member_response.data,
            "user": member_response.data.get("profiles"),
            "role": member_response.data.get("roles"),
            "permissions": permissions,
            "branches": [
                {**b["branches"], "is_primary": b["is_primary"]}
                for b in branches_response.data
            ],
        }

        logger.info(f"[USER_DETAIL] Returning user with {len(result['permissions'])} permission modules, role: {result.get('role', {}).get('name', 'N/A')}")
        return result

    @staticmethod
    async def update_member(
        org_id: UUID,
        user_id: UUID,
        data: dict,
        updated_by: UUID,
    ) -> dict:
        """
        Update organization member details.

        Args:
            org_id: Organization UUID
            user_id: User UUID
            data: Update data (title, department, employee_id, status)
            updated_by: User making the update

        Returns:
            Updated member record
        """
        # Verify member exists
        existing = (
            db.admin.table("organization_members")
            .select("*, profiles(email, full_name)")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("User not found in organization")

        # Update member
        update_data = {k: v for k, v in data.items() if v is not None}

        response = (
            db.admin.table("organization_members")
            .update(update_data)
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .execute()
        )

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=updated_by,
            action=AuditAction.UPDATE,
            resource_type="member",
            resource_id=user_id,
            resource_name=existing.data["profiles"]["full_name"] or existing.data["profiles"]["email"],
            description=f"Updated member: {existing.data['profiles']['email']}",
        )

        return response.data[0] if response.data else {}

    @staticmethod
    async def change_user_role(
        org_id: UUID,
        user_id: UUID,
        role_id: UUID,
        changed_by: UUID,
    ) -> dict:
        """
        Change a user's role in the organization.

        Args:
            org_id: Organization UUID
            user_id: User UUID
            role_id: New role UUID
            changed_by: User making the change

        Returns:
            Updated member record
        """
        # Verify member exists
        existing = (
            db.admin.table("organization_members")
            .select("*, profiles(email, full_name), roles(name)")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("User not found in organization")

        # Verify new role belongs to org
        role = (
            db.admin.table("roles")
            .select("name")
            .eq("id", str(role_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not role.data:
            raise NotFoundError("Role not found")

        # Update role
        response = (
            db.admin.table("organization_members")
            .update({"role_id": str(role_id)})
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .execute()
        )

        # Invalidate permission cache
        await cache.delete_pattern(f"permission:{user_id}:*")
        await cache.delete_pattern(f"user_permissions:{user_id}:*")

        # Log audit
        old_role_name = existing.data.get("roles", {}).get("name", "None")
        await audit_service.log(
            org_id=org_id,
            user_id=changed_by,
            action=AuditAction.ROLE_CHANGE,
            resource_type="member",
            resource_id=user_id,
            resource_name=existing.data["profiles"]["full_name"] or existing.data["profiles"]["email"],
            description=f"Changed role from '{old_role_name}' to '{role.data['name']}'",
        )

        return response.data[0] if response.data else {}

    @staticmethod
    async def remove_member(
        org_id: UUID,
        user_id: UUID,
        removed_by: UUID,
    ) -> bool:
        """
        Remove a member from the organization.

        Args:
            org_id: Organization UUID
            user_id: User UUID
            removed_by: User performing the removal

        Returns:
            True if successful
        """
        # Verify member exists
        existing = (
            db.admin.table("organization_members")
            .select("*, profiles(email, full_name)")
            .eq("org_id", str(org_id))
            .eq("user_id", str(user_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("User not found in organization")

        # Check if trying to remove owner
        org = (
            db.admin.table("organizations")
            .select("owner_id")
            .eq("id", str(org_id))
            .single()
            .execute()
        )

        if org.data and org.data.get("owner_id") == str(user_id):
            raise AuthorizationError("Cannot remove organization owner")

        # Update member status to removed
        db.admin.table("organization_members").update({
            "status": "removed",
            "removed_at": datetime.utcnow().isoformat(),
            "removed_by": str(removed_by),
        }).eq("org_id", str(org_id)).eq("user_id", str(user_id)).execute()

        # Remove from branches
        db.admin.table("user_branches").delete().eq("user_id", str(user_id)).execute()

        # Clear org_id from profile
        db.admin.table("profiles").update({
            "org_id": None,
            "primary_branch_id": None,
        }).eq("id", str(user_id)).execute()

        # Invalidate permission cache
        await cache.delete_pattern(f"permission:{user_id}:*")
        await cache.delete_pattern(f"user_permissions:{user_id}:*")

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=removed_by,
            action=AuditAction.DELETE,
            resource_type="member",
            resource_id=user_id,
            resource_name=existing.data["profiles"]["full_name"] or existing.data["profiles"]["email"],
            description=f"Removed member: {existing.data['profiles']['email']}",
        )

        return True

    # ==========================================
    # INVITATION MANAGEMENT
    # ==========================================

    @staticmethod
    async def list_invitations(
        org_id: UUID,
        status: Optional[str] = None,
    ) -> List[dict]:
        """
        List organization invitations.

        Args:
            org_id: Organization UUID
            status: Optional filter by status

        Returns:
            List of invitations
        """
        query = (
            db.admin.table("organization_invitations")
            .select("*, roles(id, name, color), branches(id, name), profiles!organization_invitations_invited_by_fkey(id, full_name, email)")
            .eq("org_id", str(org_id))
            .order("invited_at", desc=True)
        )

        if status:
            query = query.eq("status", status)

        response = query.execute()

        return [
            {
                **inv,
                "role": inv.get("roles"),
                "branch": inv.get("branches"),
                "inviter": inv.get("profiles"),
            }
            for inv in response.data
        ]

    @staticmethod
    async def create_invitation(
        org_id: UUID,
        email: str,
        role_id: UUID,
        invited_by: UUID,
        branch_id: Optional[UUID] = None,
        message: Optional[str] = None,
    ) -> dict:
        """
        Create and send an invitation.

        Args:
            org_id: Organization UUID
            email: Email to invite
            role_id: Role to assign
            invited_by: User sending the invitation
            branch_id: Optional branch to assign
            message: Optional invitation message

        Returns:
            Created invitation
        """
        # Check if user already exists in org
        existing_profile = (
            db.admin.table("profiles")
            .select("id, org_id")
            .eq("email", email.lower())
            .maybe_single()
            .execute()
        )

        if existing_profile and existing_profile.data:
            if existing_profile.data.get("org_id") == str(org_id):
                raise ConflictError("User is already a member of this organization")

        # Check for pending invitation
        existing_invite = (
            db.admin.table("organization_invitations")
            .select("id")
            .eq("org_id", str(org_id))
            .eq("email", email.lower())
            .eq("status", "pending")
            .maybe_single()
            .execute()
        )

        if existing_invite and existing_invite.data:
            raise ConflictError("An invitation is already pending for this email")

        # Verify role belongs to org
        role = (
            db.admin.table("roles")
            .select("name")
            .eq("id", str(role_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not role.data:
            raise NotFoundError("Role not found")

        # Create invitation
        invite_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=7)

        invitation_data = {
            "org_id": str(org_id),
            "email": email.lower(),
            "role_id": str(role_id),
            "branch_id": str(branch_id) if branch_id else None,
            "invite_token": invite_token,
            "status": "pending",
            "message": message,
            "invited_by": str(invited_by),
            "invited_at": datetime.utcnow().isoformat(),
            "expires_at": expires_at.isoformat(),
        }

        response = db.admin.table("organization_invitations").insert(invitation_data).execute()

        if not response.data:
            raise Exception("Failed to create invitation")

        # Send invitation email
        try:
            # Fetch details for email
            org_response = db.admin.table("organizations").select("name").eq("id", str(org_id)).single().execute()
            inviter_response = db.admin.table("profiles").select("full_name").eq("id", str(invited_by)).single().execute()
            
            org_name = org_response.data["name"] if org_response.data else "Organization"
            inviter_name = inviter_response.data["full_name"] if inviter_response.data else "An admin"
            
            # Check if user already has an account on the platform
            user_exists = existing_profile is not None and existing_profile.data is not None
            
            email_service.send_invitation_email(
                to_email=email,
                invite_token=invite_token,
                inviter_name=inviter_name,
                org_name=org_name,
                message=message,
                user_exists=user_exists
            )
        except Exception as e:
            logger.error(f"Failed to send invitation email: {e}")
            # Continue execution, don't fail the API call just because email failed

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=invited_by,
            action=AuditAction.INVITE,
            resource_type="invitation",
            resource_id=UUID(response.data[0]["id"]),
            resource_name=email,
            description=f"Sent invitation to: {email}",
        )

        return response.data[0]

    @staticmethod
    async def cancel_invitation(
        org_id: UUID,
        invitation_id: UUID,
        cancelled_by: UUID,
    ) -> bool:
        """
        Cancel a pending invitation.

        Args:
            org_id: Organization UUID
            invitation_id: Invitation UUID
            cancelled_by: User cancelling

        Returns:
            True if successful
        """
        # Verify invitation exists and is pending
        existing = (
            db.admin.table("organization_invitations")
            .select("email, status")
            .eq("id", str(invitation_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Invitation not found")

        if existing.data["status"] != "pending":
            raise ConflictError(f"Invitation is already {existing.data['status']}")

        # Update status
        db.admin.table("organization_invitations").update({
            "status": "cancelled",
        }).eq("id", str(invitation_id)).execute()

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=cancelled_by,
            action=AuditAction.UPDATE,
            resource_type="invitation",
            resource_id=invitation_id,
            resource_name=existing.data["email"],
            description=f"Cancelled invitation for: {existing.data['email']}",
        )

        return True

    @staticmethod
    async def resend_invitation(
        org_id: UUID,
        invitation_id: UUID,
        resent_by: UUID,
    ) -> dict:
        """
        Resend an invitation email.

        Args:
            org_id: Organization UUID
            invitation_id: Invitation UUID
            resent_by: User resending

        Returns:
            Updated invitation
        """
        # Verify invitation exists
        existing = (
            db.admin.table("organization_invitations")
            .select("*")
            .eq("id", str(invitation_id))
            .eq("org_id", str(org_id))
            .single()
            .execute()
        )

        if not existing.data:
            raise NotFoundError("Invitation not found")

        if existing.data["status"] != "pending":
            raise ConflictError(f"Cannot resend - invitation is {existing.data['status']}")

        # Generate new token and extend expiry
        new_token = secrets.token_urlsafe(32)
        new_expiry = datetime.utcnow() + timedelta(days=7)

        response = (
            db.admin.table("organization_invitations")
            .update({
                "invite_token": new_token,
                "expires_at": new_expiry.isoformat(),
            })
            .eq("id", str(invitation_id))
            .execute()
        )

        # Send invitation email
        try:
            # Fetch details for email
            org_response = db.admin.table("organizations").select("name").eq("id", str(org_id)).single().execute()
            resent_by_response = db.admin.table("profiles").select("full_name").eq("id", str(resent_by)).single().execute()
            
            org_name = org_response.data["name"] if org_response.data else "Organization"
            resent_by_name = resent_by_response.data["full_name"] if resent_by_response.data else "An admin"
            
            # Check if user already has an account on the platform
            user_profile = (
                db.admin.table("profiles")
                .select("id")
                .eq("email", existing.data["email"].lower())
                .maybe_single()
                .execute()
            )
            user_exists = user_profile is not None and user_profile.data is not None
            
            email_service.send_invitation_email(
                to_email=existing.data["email"],
                invite_token=new_token,
                inviter_name=resent_by_name,
                org_name=org_name,
                message="Invitation resent.",
                user_exists=user_exists
            )
        except Exception as e:
            logger.error(f"Failed to resend invitation email: {e}")

        # Log audit
        await audit_service.log(
            org_id=org_id,
            user_id=resent_by,
            action=AuditAction.UPDATE,
            resource_type="invitation",
            resource_id=invitation_id,
            resource_name=existing.data["email"],
            description=f"Resent invitation to: {existing.data['email']}",
        )

        return response.data[0] if response.data else existing.data

    # ==========================================
    # ROLE MANAGEMENT
    # ==========================================

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

    @staticmethod
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

    @staticmethod
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


# Global admin service instance
admin_service = AdminService()
