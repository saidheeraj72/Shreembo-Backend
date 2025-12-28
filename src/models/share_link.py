"""Share link models."""
from typing import Optional
from uuid import UUID
from datetime import datetime
from enum import Enum
from pydantic import BaseModel


class SharePermission(str, Enum):
    VIEW = "view"
    COMMENT = "comment"
    EDIT = "edit"
    ADMIN = "admin"


class ShareLinkCreate(BaseModel):
    node_id: UUID
    permission: SharePermission = SharePermission.VIEW
    password: Optional[str] = None
    expires_in_days: Optional[int] = None
    max_access_count: Optional[int] = None
    name: Optional[str] = None


class ShareLinkResponse(BaseModel):
    id: UUID
    node_id: UUID
    token: str
    permission: SharePermission
    has_password: bool = False
    expires_at: Optional[datetime] = None
    max_access_count: Optional[int] = None
    access_count: int = 0
    is_active: bool = True
    name: Optional[str] = None
    created_by: UUID
    created_at: datetime
    url: str

    class Config:
        from_attributes = True


class ShareLinkAccess(BaseModel):
    password: Optional[str] = None
