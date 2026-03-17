"""Composed group service."""

from .group_readwrite import GroupReadWriteMixin
from .group_delete import GroupDeleteMixin
from .group_members import GroupMembersMixin


class GroupService(GroupReadWriteMixin, GroupDeleteMixin, GroupMembersMixin):
    """Service for managing user groups."""


group_service = GroupService()
