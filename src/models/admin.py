"""
Admin panel Pydantic models for branches, users, roles, and invitations.
"""
from datetime import datetime
from typing import Optional, List
from uuid import UUID
from enum import Enum
from pydantic import BaseModel, EmailStr, Field

from src.models.common import UUIDModel, TimestampMixin


# ==========================================
# BRANCH MODELS
# ==========================================

class BranchType(str, Enum):
    """Branch type enum."""
    HEADQUARTERS = "headquarters"
    OFFICE = "office"
    WAREHOUSE = "warehouse"
    STORE = "store"
    REMOTE = "remote"
    DEPARTMENT = "department"


class BranchBase(BaseModel):
    """Base branch model."""
    name: str = Field(..., min_length=1, max_length=255)
    code: Optional[str] = Field(None, max_length=50)
    branch_type: BranchType = BranchType.OFFICE
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "US"
    postal_code: Optional[str] = None
    timezone: str = "UTC"
    phone: Optional[str] = None
    email: Optional[EmailStr] = None


class BranchCreate(BranchBase):
    """Branch creation model."""
    manager_id: Optional[UUID] = None
    parent_branch_id: Optional[UUID] = None


class BranchUpdate(BaseModel):
    """Branch update model."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    code: Optional[str] = Field(None, max_length=50)
    branch_type: Optional[BranchType] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    timezone: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    manager_id: Optional[UUID] = None
    parent_branch_id: Optional[UUID] = None
    is_active: Optional[bool] = None


class Branch(UUIDModel, BranchBase, TimestampMixin):
    """Branch response model."""
    org_id: UUID
    manager_id: Optional[UUID] = None
    parent_branch_id: Optional[UUID] = None
    is_active: bool = True

    class Config:
        from_attributes = True


class BranchWithManager(Branch):
    """Branch with manager details."""
    manager: Optional[dict] = None
    user_count: int = 0


class BranchUserAssignment(BaseModel):
    """Branch user assignment request."""
    user_id: UUID
    is_primary: bool = False


# ==========================================
# USER/MEMBER MODELS
# ==========================================

class MemberStatus(str, Enum):
    """Member status enum."""
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class OrganizationMemberResponse(UUIDModel):
    """Organization member response model."""
    org_id: UUID
    user_id: UUID
    role_id: Optional[UUID] = None
    status: MemberStatus = MemberStatus.ACTIVE
    title: Optional[str] = None
    department: Optional[str] = None
    employee_id: Optional[str] = None
    joined_at: Optional[datetime] = None

    # Nested user profile
    user: Optional[dict] = None
    role: Optional[dict] = None
    branches: List[dict] = Field(default_factory=list)
    permissions: Optional[dict] = None

    class Config:
        from_attributes = True


class MemberUpdate(BaseModel):
    """Member update model."""
    title: Optional[str] = None
    department: Optional[str] = None
    employee_id: Optional[str] = None
    status: Optional[MemberStatus] = None


class RoleChangeRequest(BaseModel):
    """Role change request model."""
    role_id: UUID


class UserWithRole(BaseModel):
    """User with role information."""
    id: UUID
    email: str
    full_name: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    status: str
    role: Optional[dict] = None
    title: Optional[str] = None
    department: Optional[str] = None
    joined_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None


class UserPermissionGrant(BaseModel):
    """User permission grant request."""
    permission_id: UUID
    is_granted: bool = True


# ==========================================
# INVITATION MODELS
# ==========================================

class InvitationStatus(str, Enum):
    """Invitation status enum."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class InvitationCreate(BaseModel):
    """Invitation creation model."""
    email: EmailStr
    role_id: UUID
    branch_id: Optional[UUID] = None
    message: Optional[str] = None


class InvitationResponse(UUIDModel):
    """Invitation response model."""
    org_id: UUID
    email: str
    role_id: Optional[UUID] = None
    branch_id: Optional[UUID] = None
    status: InvitationStatus = InvitationStatus.PENDING
    message: Optional[str] = None
    invited_by: UUID
    invited_at: datetime
    expires_at: datetime
    accepted_at: Optional[datetime] = None

    # Nested details
    role: Optional[dict] = None
    branch: Optional[dict] = None
    inviter: Optional[dict] = None

    class Config:
        from_attributes = True


class InvitationListItem(BaseModel):
    """Simplified invitation for lists."""
    id: UUID
    email: str
    status: InvitationStatus
    role_name: Optional[str] = None
    branch_name: Optional[str] = None
    invited_at: datetime
    expires_at: datetime


# ==========================================
# ROLE MODELS
# ==========================================

class RoleCreate(BaseModel):
    """Role creation model."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    color: str = "#6366f1"
    icon: Optional[str] = None
    permission_ids: List[UUID] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """Role update model."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    permission_ids: Optional[List[UUID]] = None


class RoleResponse(UUIDModel, TimestampMixin):
    """Role response model."""
    org_id: UUID
    name: str
    slug: str
    description: Optional[str] = None
    color: str = "#6366f1"
    icon: Optional[str] = None
    is_system_role: bool = False
    is_custom_role: bool = True
    priority: int = 0
    permission_count: int = 0
    user_count: int = 0

    class Config:
        from_attributes = True


class RoleWithPermissions(RoleResponse):
    """Role with full permissions list."""
    permissions: List[dict] = Field(default_factory=list)


class PermissionModule(BaseModel):
    """Permission module with actions."""
    id: UUID
    key: str
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    category: Optional[str] = None
    permissions: List[dict] = Field(default_factory=list)


class PermissionsListResponse(BaseModel):
    """Full permissions list grouped by module."""
    modules: List[PermissionModule] = Field(default_factory=list)
