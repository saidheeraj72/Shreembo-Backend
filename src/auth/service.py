"""Composed auth service."""

from .login import AuthLoginMixin
from .signup import AuthSignupMixin
from .current_user import AuthCurrentUserMixin
from .invite_verify import AuthInviteVerifyMixin
from .invite_accept import AuthInviteAcceptMixin
from .organizations import AuthOrganizationsMixin
from .create_organization import AuthCreateOrganizationMixin


class AuthService(
    AuthLoginMixin,
    AuthSignupMixin,
    AuthCurrentUserMixin,
    AuthInviteVerifyMixin,
    AuthInviteAcceptMixin,
    AuthOrganizationsMixin,
    AuthCreateOrganizationMixin,
):
    """Service for auth, sessions, and org membership."""


# Bind class name into part modules for methods that reference AuthService
from . import invite_accept as _invite_accept
from . import organizations as _organizations
from . import create_organization as _create_organization

_invite_accept.AuthService = AuthService
_organizations.AuthService = AuthService
_create_organization.AuthService = AuthService

# Global auth service instance
auth_service = AuthService()
