"""Composed admin service."""

from .branches_read import AdminBranchesReadMixin
from .branches_write import AdminBranchesWriteMixin
from .branch_assignments import AdminBranchAssignmentsMixin
from .users import AdminUsersMixin
from .user_roles import AdminUserRolesMixin
from .invitations import AdminInvitationsMixin
from .invitation_resend import AdminInvitationResendMixin
from .roles_read import AdminRolesReadMixin
from .roles_write import AdminRolesWriteMixin
from .roles_admin import AdminRolesAdminMixin


class AdminService(
    AdminBranchesReadMixin,
    AdminBranchesWriteMixin,
    AdminBranchAssignmentsMixin,
    AdminUsersMixin,
    AdminUserRolesMixin,
    AdminInvitationsMixin,
    AdminInvitationResendMixin,
    AdminRolesReadMixin,
    AdminRolesWriteMixin,
    AdminRolesAdminMixin,
):
    """Service for organization admin operations."""


from . import roles_write as _roles_write
_roles_write.AdminService = AdminService

admin_service = AdminService()
