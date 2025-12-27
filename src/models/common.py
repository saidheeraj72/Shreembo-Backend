"""
Common Pydantic models used across the application.
"""
from datetime import datetime
from typing import Generic, TypeVar, Optional, List
from uuid import UUID
from pydantic import BaseModel, Field


# Generic type for paginated responses
T = TypeVar("T")


class PaginationParams(BaseModel):
    """Pagination query parameters."""

    page: int = Field(default=1, ge=1, description="Page number")
    limit: int = Field(default=50, ge=1, le=100, description="Items per page")

    @property
    def offset(self) -> int:
        """Calculate offset from page and limit."""
        return (self.page - 1) * self.limit


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response wrapper."""

    items: List[T]
    total: int
    page: int
    limit: int
    pages: int

    @classmethod
    def create(
        cls,
        items: List[T],
        total: int,
        page: int,
        limit: int,
    ) -> "PaginatedResponse[T]":
        """
        Create paginated response.

        Args:
            items: List of items
            total: Total count
            page: Current page
            limit: Items per page

        Returns:
            PaginatedResponse instance
        """
        pages = (total + limit - 1) // limit  # Ceiling division
        return cls(
            items=items,
            total=total,
            page=page,
            limit=limit,
            pages=pages,
        )


class TimestampMixin(BaseModel):
    """Mixin for models with timestamps."""

    created_at: datetime
    updated_at: datetime


class UUIDModel(BaseModel):
    """Base model with UUID identifier."""

    id: UUID


class SuccessResponse(BaseModel):
    """Generic success response."""

    success: bool = True
    message: str


class ErrorResponse(BaseModel):
    """Generic error response."""

    detail: str
    error: Optional[dict] = None


class StatusEnum(BaseModel):
    """Base enum for status fields."""

    pass
