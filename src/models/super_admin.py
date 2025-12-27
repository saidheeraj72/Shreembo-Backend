"""
Super Admin Pydantic models.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr

from src.models.common import UUIDModel, TimestampMixin


class SuperAdminBase(BaseModel):
    """Base super admin model."""

    email: EmailStr


class SuperAdminCreate(SuperAdminBase):
    """Super admin creation model."""

    full_name: Optional[str] = None


class SuperAdmin(UUIDModel, SuperAdminBase):
    """Super admin response model."""

    full_name: Optional[str] = None
    is_active: bool = True
    created_at: datetime
    last_login_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SuperAdminVerifyResponse(BaseModel):
    """Super admin verification response."""

    is_super_admin: bool
    email: str
