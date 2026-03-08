"""Auto-split auth service part."""
from datetime import datetime, timedelta
from typing import Optional, Dict
from uuid import UUID
import logging

from src.core.database import db
from src.core.exceptions import AuthenticationError, ConflictError, NotFoundError, ValidationError
from src.audit.service import audit_service
from src.models.audit import AuditAction

logger = logging.getLogger(__name__)


class AuthCreateOrganizationMixin:
    @staticmethod
    async def create_organization(
        user_id: UUID,
        name: str,
        slug: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> Dict:
        """
        Create a new organization with the user as owner.

        Args:
            user_id: User creating the organization
            name: Organization name
            slug: URL-friendly slug (auto-generated if not provided)
            domain: Organization domain (optional)

        Returns:
            Dictionary with organization and updated user data
        """
        from src.core.exceptions import ConflictError
        import re

        # Generate slug if not provided
        if not slug:
            slug = re.sub(r'[^a-z0-9-]', '-', name.lower())
            slug = re.sub(r'-+', '-', slug).strip('-')

        # Check if slug already exists
        existing_slug = (
            db.admin.table("organizations")
            .select("id")
            .eq("slug", slug)
            .maybe_single()
            .execute()
        )

        if existing_slug and existing_slug.data:
            # Add random suffix to make unique
            import random
            import string
            suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
            slug = f"{slug}-{suffix}"

        # Check if domain already exists (if provided)
        if domain:
            existing_domain = (
                db.admin.table("organizations")
                .select("id")
                .eq("domain", domain.lower())
                .maybe_single()
                .execute()
            )

            if existing_domain and existing_domain.data:
                raise ConflictError(f"Domain '{domain}' is already registered to another organization")

        # Create organization
        org_data = {
            "name": name,
            "slug": slug,
            "domain": domain.lower() if domain else None,
            "owner_id": str(user_id),
            "plan_type": "free",
            "subscription_status": "trial",
            "user_limit": 5,
            "storage_limit_gb": 10,
            "is_active": True,
        }

        org_response = db.admin.table("organizations").insert(org_data).execute()

        if not org_response.data:
            raise Exception("Failed to create organization")

        org = org_response.data[0]
        org_id = org["id"]

        # Create default roles for the organization
        default_roles = [
            {
                "org_id": org_id,
                "name": "Owner",
                "slug": "owner",
                "description": "Organization owner with full access",
                "is_system_role": True,
                "is_custom_role": False,
                "priority": 100,
                "color": "#dc2626",
            },
            {
                "org_id": org_id,
                "name": "Admin",
                "slug": "admin",
                "description": "Administrator with management access",
                "is_system_role": True,
                "is_custom_role": False,
                "priority": 90,
                "color": "#ea580c",
            },
            {
                "org_id": org_id,
                "name": "Member",
                "slug": "member",
                "description": "Regular member with standard access",
                "is_system_role": True,
                "is_custom_role": False,
                "priority": 10,
                "color": "#6366f1",
            },
        ]

        for role in default_roles:
            db.admin.table("roles").insert(role).execute()

        # Get the owner role
        owner_role_response = (
            db.admin.table("roles")
            .select("id")
            .eq("org_id", org_id)
            .eq("slug", "owner")
            .single()
            .execute()
        )

        owner_role_id = owner_role_response.data["id"] if owner_role_response.data else None

        # Add user as organization member (owner)
        membership_data = {
            "org_id": org_id,
            "user_id": str(user_id),
            "role_id": owner_role_id,
            "status": "active",
            "joined_at": datetime.utcnow().isoformat(),
        }

        db.admin.table("organization_members").insert(membership_data).execute()

        # Update user profile to point to new org
        db.admin.table("profiles").update({
            "org_id": org_id,
            "account_type": "organization",
        }).eq("id", str(user_id)).execute()

        # Get updated user with permissions
        updated_user = await AuthService.get_current_user_with_permissions(user_id, UUID(org_id))

        # Log the creation
        await audit_service.log(
            org_id=UUID(org_id),
            user_id=user_id,
            user_email=updated_user.get("email"),
            user_name=updated_user.get("full_name"),
            action=AuditAction.CREATE,
            resource_type="organization",
            resource_id=UUID(org_id),
            description=f"Created organization: {name}",
        )

        return {
            "organization": org,
            "user": updated_user,
        }
