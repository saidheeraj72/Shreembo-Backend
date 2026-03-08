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


class AuthCurrentUserMixin:
    async def get_current_user_with_permissions(user_id: UUID, org_id: Optional[UUID]) -> Dict:
        """
        Get user profile with permissions and super admin status.

        Args:
            user_id: User ID
            org_id: Organization ID

        Returns:
            User profile with permissions and super admin status
        """
        from src.access.permission import permission_service
        from src.super_admin.service import super_admin_service

        # Get user profile
        profile_response = (
            db.admin.table("profiles")
            .select("*")
            .eq("id", str(user_id))
            .single()
            .execute()
        )

        user_data = profile_response.data

        # Check if user is super admin
        is_super_admin = await super_admin_service.verify_super_admin(user_data["email"])
        user_data["is_super_admin"] = is_super_admin

        # Get permissions if user is in an organization
        if org_id:
            permissions = await permission_service.get_user_permissions(user_id, org_id)
            user_data["permissions"] = permissions
        else:
            user_data["permissions"] = {}

        # Super admins get access to super_admin module
        if is_super_admin:
            if "super_admin" not in user_data["permissions"]:
                user_data["permissions"]["super_admin"] = {}
            user_data["permissions"]["super_admin"]["access"] = True

        return user_data

