"""
Audit log Pydantic models.
"""
from datetime import datetime, date
from typing import Optional, Dict, Any
from uuid import UUID
from enum import Enum
from pydantic import BaseModel, Field

from src.models.common import UUIDModel, PaginationParams


class AuditAction(str, Enum):
    """Audit action types."""

    # General
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    VIEW = "view"
    DOWNLOAD = "download"
    SHARE = "share"
    UPLOAD = "upload"

    # Auth
    LOGIN = "login"
    LOGOUT = "logout"

    # Users
    INVITE = "invite"
    PERMISSION_CHANGE = "permission_change"
    ROLE_CHANGE = "role_change"

    # Documents
    ARCHIVE = "archive"
    RESTORE = "restore"
    EXPORT = "export"
    IMPORT = "import"


class LogLevel(str, Enum):
    """Log severity level."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditLogCreate(BaseModel):
    """Audit log creation model."""

    org_id: Optional[UUID] = None
    user_id: UUID
    user_email: str
    user_name: Optional[str] = None
    action: AuditAction
    resource_type: str
    resource_id: Optional[UUID] = None
    resource_name: Optional[str] = None
    description: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    changes: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    location: Optional[str] = None
    severity: LogLevel = LogLevel.INFO


class AuditLog(UUIDModel):
    """Audit log response model."""

    org_id: Optional[UUID]
    user_id: Optional[UUID]
    user_email: Optional[str]
    user_name: Optional[str]
    action: AuditAction
    resource_type: str
    resource_id: Optional[UUID]
    resource_name: Optional[str]
    description: Optional[str]
    details: Dict[str, Any] = Field(default_factory=dict)
    changes: Optional[Dict[str, Any]] = None
    ip_address: Optional[str]
    user_agent: Optional[str]
    location: Optional[str]
    severity: LogLevel
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogFilters(PaginationParams):
    """Audit log filter parameters."""

    action: Optional[AuditAction] = None
    resource_type: Optional[str] = None
    user_id: Optional[UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    severity: Optional[LogLevel] = None


class AuditLogExportRequest(BaseModel):
    """Audit log export request."""

    format: str = Field(default="csv", pattern="^(csv|json)$")
    filters: Optional[AuditLogFilters] = None
