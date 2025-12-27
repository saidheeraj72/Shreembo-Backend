"""
Audit logging service for tracking all system actions.
"""
from datetime import datetime
from typing import Optional, Dict, Any, List
from uuid import UUID

from src.core.database import db
from src.models.audit import AuditAction, LogLevel, AuditLogFilters


class AuditService:
    """Service for managing audit logs."""

    @staticmethod
    async def log(
        org_id: Optional[UUID],
        user_id: UUID,
        action: AuditAction,
        resource_type: str,
        resource_id: Optional[UUID] = None,
        resource_name: Optional[str] = None,
        description: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        changes: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        location: Optional[str] = None,
        severity: LogLevel = LogLevel.INFO,
        user_name: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> dict:
        """
        Create an audit log entry.

        Args:
            org_id: Organization ID (None for personal/platform actions)
            user_id: User performing the action
            action: Action performed
            resource_type: Type of resource (e.g., 'file', 'user', 'role')
            resource_id: ID of the resource
            resource_name: Name of the resource
            description: Human-readable description
            details: Additional details (JSON)
            changes: Before/after changes for updates
            ip_address: Client IP address
            user_agent: Client user agent
            location: Geographic location (optional)
            severity: Log severity level
            user_name: User full name

        Returns:
            Created audit log entry
        """
        log_data = {
            "org_id": str(org_id) if org_id else None,
            "user_id": str(user_id),
            "user_email": user_email,
            "user_name": user_name,
            "action": action.value if isinstance(action, AuditAction) else action,
            "resource_type": resource_type,
            "resource_id": str(resource_id) if resource_id else None,
            "resource_name": resource_name,
            "description": description,
            "details": details or {},
            "changes": changes,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "location": location,
            "severity": severity.value if isinstance(severity, LogLevel) else severity,
        }

        response = db.admin.table("audit_logs").insert(log_data).execute()
        return response.data[0] if response.data else None

    @staticmethod
    async def get_logs(
        org_id: UUID,
        filters: AuditLogFilters,
    ) -> tuple[List[dict], int]:
        """
        Get audit logs with filters.

        Args:
            org_id: Organization ID
            filters: Filter parameters

        Returns:
            Tuple of (logs, total_count)
        """
        # Build query
        query = db.admin.table("audit_logs").select("*", count="exact")

        # Apply filters
        if org_id:
            query = query.eq("org_id", str(org_id))

        if filters.action:
            query = query.eq("action", filters.action.value)

        if filters.resource_type:
            query = query.eq("resource_type", filters.resource_type)

        if filters.user_id:
            query = query.eq("user_id", str(filters.user_id))

        if filters.severity:
            query = query.eq("severity", filters.severity.value)

        if filters.start_date:
            query = query.gte("created_at", filters.start_date.isoformat())

        if filters.end_date:
            # Add one day to include the entire end date
            end_datetime = datetime.combine(
                filters.end_date,
                datetime.max.time()
            )
            query = query.lte("created_at", end_datetime.isoformat())

        # Order by most recent first
        query = query.order("created_at", desc=True)

        # Apply pagination
        query = query.range(filters.offset, filters.offset + filters.limit - 1)

        # Execute query
        response = query.execute()

        return response.data, response.count or 0

    @staticmethod
    async def get_log_by_id(log_id: UUID) -> Optional[dict]:
        """
        Get a specific audit log by ID.

        Args:
            log_id: Audit log ID

        Returns:
            Audit log entry or None
        """
        response = (
            db.admin.table("audit_logs")
            .select("*")
            .eq("id", str(log_id))
            .maybe_single()
            .execute()
        )
        return response.data

    @staticmethod
    async def get_resource_history(
        resource_type: str,
        resource_id: UUID,
        limit: int = 50,
    ) -> List[dict]:
        """
        Get audit history for a specific resource.

        Args:
            resource_type: Type of resource
            resource_id: Resource ID
            limit: Maximum number of entries

        Returns:
            List of audit log entries
        """
        response = (
            db.admin.table("audit_logs")
            .select("*")
            .eq("resource_type", resource_type)
            .eq("resource_id", str(resource_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data

    @staticmethod
    async def get_user_activity(
        user_id: UUID,
        org_id: Optional[UUID] = None,
        limit: int = 100,
    ) -> List[dict]:
        """
        Get recent activity for a user.

        Args:
            user_id: User ID
            org_id: Organization ID (optional)
            limit: Maximum number of entries

        Returns:
            List of audit log entries
        """
        query = (
            db.admin.table("audit_logs")
            .select("*")
            .eq("user_id", str(user_id))
        )

        if org_id:
            query = query.eq("org_id", str(org_id))

        response = (
            query.order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data


# Global audit service instance
audit_service = AuditService()
