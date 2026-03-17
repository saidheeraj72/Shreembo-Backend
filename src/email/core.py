"""Email delivery core helpers."""
import logging

import resend

from src.config import settings

logger = logging.getLogger(__name__)


class EmailCoreMixin:
    def __init__(self):
        if settings.RESEND_API_KEY:
            resend.api_key = settings.RESEND_API_KEY

    def send_email(self, to_email: str, subject: str, html_content: str):
        if not settings.EMAIL_ENABLED:
            logger.warning("Email is disabled. Email not sent.")
            return
        if not settings.RESEND_API_KEY:
            logger.warning("RESEND_API_KEY not configured. Email not sent.")
            return

        try:
            params = {
                "from": f"{settings.RESEND_FROM_NAME} <{settings.RESEND_FROM_EMAIL}>",
                "to": [to_email],
                "subject": subject,
                "html": html_content,
            }
            email = resend.Emails.send(params)
            logger.info("Email sent successfully to %s, id: %s", to_email, email.get("id"))
            return email
        except Exception as e:
            logger.error("Failed to send email to %s: %s", to_email, str(e))
