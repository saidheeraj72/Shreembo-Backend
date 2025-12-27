"""
Super Admin service for platform-level administration.
"""
from typing import List, Optional
from uuid import UUID

from src.core.database import db
from src.core.exceptions import AuthorizationError, NotFoundError


class SuperAdminService:
    """Service for super admin operations."""

    @staticmethod
    async def verify_super_admin(email: str) -> bool:
        """
        Check if an email belongs to a super admin.

        Args:
            email: Email address to check

        Returns:
            True if the email is a super admin
        """
        response = (
            db.admin.table("super_admins")
            .select("id, is_active")
            .eq("email", email.lower())
            .eq("is_active", True)
            .maybe_single()
            .execute()
        )

        return response is not None and response.data is not None

    @staticmethod
    async def get_super_admin_by_email(email: str) -> Optional[dict]:
        """
        Get super admin details by email.

        Args:
            email: Email address

        Returns:
            Super admin data or None
        """
        response = (
            db.admin.table("super_admins")
            .select("*")
            .eq("email", email.lower())
            .maybe_single()
            .execute()
        )

        return response.data if response else None

    @staticmethod
    async def create_super_admin(email: str, full_name: Optional[str] = None) -> dict:
        """
        Create a new super admin.

        Args:
            email: Email address
            full_name: Full name (optional)

        Returns:
            Created super admin data
        """
        data = {
            "email": email.lower(),
            "full_name": full_name,
            "is_active": True,
        }

        response = db.admin.table("super_admins").insert(data).execute()

        if not response.data:
            raise Exception("Failed to create super admin")

        return response.data[0]

    @staticmethod
    async def list_super_admins() -> List[dict]:
        """
        List all super admins.

        Returns:
            List of super admin data
        """
        response = (
            db.admin.table("super_admins")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )

        return response.data

    @staticmethod
    async def deactivate_super_admin(super_admin_id: UUID) -> dict:
        """
        Deactivate a super admin.

        Args:
            super_admin_id: Super admin UUID

        Returns:
            Updated super admin data
        """
        response = (
            db.admin.table("super_admins")
            .update({"is_active": False})
            .eq("id", str(super_admin_id))
            .execute()
        )

        if not response.data:
            raise NotFoundError("Super admin not found")

        return response.data[0]

    @staticmethod
    async def activate_super_admin(super_admin_id: UUID) -> dict:
        """
        Activate a super admin.

        Args:
            super_admin_id: Super admin UUID

        Returns:
            Updated super admin data
        """
        response = (
            db.admin.table("super_admins")
            .update({"is_active": True})
            .eq("id", str(super_admin_id))
            .execute()
        )

        if not response.data:
            raise NotFoundError("Super admin not found")

        return response.data[0]

    @staticmethod
    async def update_last_login(email: str) -> None:
        """
        Update last login timestamp for super admin.

        Args:
            email: Super admin email
        """
        from datetime import datetime

        db.admin.table("super_admins").update(
            {"last_login_at": datetime.utcnow().isoformat()}
        ).eq("email", email.lower()).execute()


# Global super admin service instance
super_admin_service = SuperAdminService()
