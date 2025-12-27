"""
Group management Pydantic models.
"""
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field

from src.models.common import UUIDModel, TimestampMixin


class GroupBase(BaseModel):
    """Base group model."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    color: str = "#6366f1"
    icon: Optional[str] = None
    group_type: str = "custom"


class GroupCreate(GroupBase):
    """Group creation model."""
    member_ids: List[UUID] = Field(default_factory=list)


class GroupUpdate(BaseModel):
    """Group update model."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    group_type: Optional[str] = None


class GroupMember(BaseModel):
    """Group member model."""
    group_id: UUID
    user_id: UUID
    is_admin: bool = False
    joined_at: datetime
    
    # Joined data
    user: Optional[dict] = None

    class Config:
        from_attributes = True


class Group(UUIDModel, GroupBase, TimestampMixin):
    """Group response model."""
    org_id: UUID
    slug: str
    member_count: int = 0
    created_by: Optional[UUID] = None
    
    # Optional nested data
    members: List[GroupMember] = Field(default_factory=list)

    class Config:
        from_attributes = True


class GroupMemberAdd(BaseModel):
    """Request model for adding members to a group."""
    user_ids: List[UUID]


class GroupMemberRemove(BaseModel):
    """Request model for removing members from a group."""
    user_ids: List[UUID]
