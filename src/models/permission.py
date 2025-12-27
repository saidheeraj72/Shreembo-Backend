"""
Permission and role Pydantic models.
"""
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field

from src.models.common import UUIDModel, TimestampMixin


class PermissionModule(UUIDModel):
    """Permission module model."""

    key: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    category: Optional[str] = None
    sort_order: int = 0

    class Config:
        from_attributes = True


class Permission(UUIDModel):
    """Permission model."""

    module_id: UUID
    action: str
    name: str
    description: Optional[str] = None
    is_dangerous: bool = False
    requires_2fa: bool = False
    sort_order: int = 0

    class Config:
        from_attributes = True


class PermissionWithModule(Permission):
    """Permission with module details."""

    module: PermissionModule


class RoleBase(BaseModel):
    """Base role model."""

    name: str
    slug: str
    description: Optional[str] = None
    color: str = "#6366f1"
    icon: Optional[str] = None


class RoleCreate(RoleBase):
    """Role creation model."""

    permission_ids: List[UUID] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """Role update model."""

    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    permission_ids: Optional[List[UUID]] = None


class Role(UUIDModel, RoleBase, TimestampMixin):
    """Role response model."""

    org_id: UUID
    is_system_role: bool = False
    is_custom_role: bool = True
    priority: int = 0

    class Config:
        from_attributes = True


class RoleWithPermissions(Role):
    """Role with permissions list."""

    permissions: List[PermissionWithModule] = Field(default_factory=list)
    user_count: int = 0


class UserPermissionOverride(BaseModel):
    """User permission override model."""

    user_id: UUID
    permission_id: UUID
    is_granted: bool = True
    granted_by: Optional[UUID] = None


class PermissionCheckRequest(BaseModel):
    """Permission check request."""

    module: str
    action: str


class PermissionCheckResponse(BaseModel):
    """Permission check response."""

    has_permission: bool
    module: str
    action: str
