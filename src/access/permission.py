"""Composed permission service."""

from .permission_checks import PermissionChecksMixin
from .permission_overrides import PermissionOverridesMixin
from .folder_access import PermissionFolderAccessMixin
from .folder_checks import PermissionFolderChecksMixin


class PermissionService(
    PermissionChecksMixin,
    PermissionOverridesMixin,
    PermissionFolderAccessMixin,
    PermissionFolderChecksMixin,
):
    """Service for permission and folder access controls."""


from . import folder_checks as _folder_checks

_folder_checks.PermissionService = PermissionService

# Global singleton
permission_service = PermissionService()
