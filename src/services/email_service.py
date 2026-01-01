import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
import logging

from src.config import settings

logger = logging.getLogger(__name__)

class EmailService:
    def send_email(self, to_email: str, subject: str, html_content: str):
        if not settings.SMTP_ENABLED:
            logger.warning("SMTP is disabled. Email not sent.")
            return

        try:
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL or settings.SMTP_USER}>"
            message["To"] = to_email

            part = MIMEText(html_content, "html")
            message.attach(part)

            # Connect to SMTP server
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(
                    settings.SMTP_FROM_EMAIL or settings.SMTP_USER,
                    to_email,
                    message.as_string()
                )
            
            logger.info(f"Email sent successfully to {to_email}")

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            # Don't raise exception to avoid breaking the calling flow, but log it error
            # In production, we might want to retry or raise.

    def send_invitation_email(self, to_email: str, invite_token: str, inviter_name: str, org_name: str, message: Optional[str] = None):
        invite_link = f"{settings.FRONTEND_URL}/accept-invite/{invite_token}"
        
        # Log the link for development/debugging purposes
        logger.info(f"🔗 GENERATED INVITE LINK: {invite_link}")
        
        subject = f"Invitation to join {org_name} on {settings.PROJECT_NAME}"
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2>You've been invited!</h2>
            <p><strong>{inviter_name}</strong> has invited you to join <strong>{org_name}</strong> on {settings.PROJECT_NAME}.</p>
            
            {f'<p><em>"{message}"</em></p>' if message else ''}
            
            <p>Click the button below to accept the invitation and set up your account:</p>
            
            <div style="text-align: center; margin: 30px 0;">
                <a href="{invite_link}" style="background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold;">Accept Invitation</a>
            </div>
            
            <p>Or copy and paste this link into your browser:</p>
            <p><a href="{invite_link}">{invite_link}</a></p>
            
            <p>This link will expire in {settings.INVITATION_EXPIRY_DAYS} days.</p>
            
            <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
            <p style="color: #666; font-size: 12px;">If you didn't expect this invitation, you can ignore this email.</p>
        </div>
        """
        
        self.send_email(to_email, subject, html_content)

email_service = EmailService()
