"""Composed email service."""

from .core import EmailCoreMixin
from .invitation import EmailInvitationMixin
from .welcome import EmailWelcomeMixin
from .org_approved import EmailOrgApprovedMixin


class EmailService(
    EmailCoreMixin,
    EmailInvitationMixin,
    EmailWelcomeMixin,
    EmailOrgApprovedMixin,
):
    """Service for transactional email delivery and templates."""


email_service = EmailService()
