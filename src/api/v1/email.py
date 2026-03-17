"""
Email Writer API routes.
User-level, stateless — no history stored.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.core.dependencies import get_current_user_id
from src.models.email import EmailGenerateRequest, EmailGenerateResponse
from src.email_writer.service import email_writer_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/generate",
    response_model=EmailGenerateResponse,
    summary="Generate an email",
    description="Generate a professional email based on key points, tone, and type.",
)
async def generate_email(
    request: EmailGenerateRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> EmailGenerateResponse:
    """Generate an email for the authenticated user."""
    try:
        generated_email = await email_writer_service.generate_email(request)

        return EmailGenerateResponse(
            generated_email=generated_email,
            subject=request.subject,
        )
    except Exception as e:
        logger.exception("Email generation failed for user %s: %s", user_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate email. Please try again.",
        )
