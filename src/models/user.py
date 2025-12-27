"""
User and profile Pydantic models.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID
from enum import Enum
from pydantic import BaseModel, EmailStr, Field

from src.models.common import UUIDModel, TimestampMixin


class AccountType(str, Enum):
    """User account type."""

    PERSONAL = "personal"
    ORGANIZATION = "organization"


class UserStatus(str, Enum):
    """User status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class UserBase(BaseModel):
    """Base user model."""

    email: EmailStr
    full_name: Optional[str] = None
    display_name: Optional[str] = None
    phone: Optional[str] = None


class UserCreate(UserBase):
    """User creation model."""

    password: str = Field(..., min_length=8)
    account_type: AccountType = AccountType.PERSONAL


class UserUpdate(BaseModel):
    """User update model."""

    full_name: Optional[str] = None
    display_name: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None


class UserProfile(UUIDModel, UserBase, TimestampMixin):
    """User profile response model."""

    avatar_url: Optional[str] = None
    account_type: AccountType
    org_id: Optional[UUID] = None
    primary_branch_id: Optional[UUID] = None
    status: UserStatus
    email_verified: bool = False
    phone_verified: bool = False
    two_factor_enabled: bool = False
    last_login_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserWithPermissions(UserProfile):
    """User profile with permissions."""

    permissions: dict = Field(default_factory=dict)
    role: Optional[dict] = None


class UserListItem(UUIDModel):
    """User list item (simplified)."""

    email: EmailStr
    full_name: Optional[str]
    avatar_url: Optional[str]
    status: UserStatus
    last_active_at: Optional[datetime]
