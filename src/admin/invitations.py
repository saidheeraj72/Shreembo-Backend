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


class AdminInvitationsMixin:
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

