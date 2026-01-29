"""
Email service using Resend for sending transactional emails.
"""
import resend
from typing import Optional
import logging

from src.config import settings

logger = logging.getLogger(__name__)


class EmailService:
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
            logger.info(f"Email sent successfully to {to_email}, id: {email.get('id')}")
            return email

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            # Don't raise exception to avoid breaking the calling flow

    def send_invitation_email(
        self,
        to_email: str,
        invite_token: str,
        inviter_name: str,
        org_name: str,
        message: Optional[str] = None,
        user_exists: bool = False,
    ):
        invite_link = f"{settings.FRONTEND_URL}/accept-invite/{invite_token}"

        # Log the link for development/debugging purposes
        logger.info(f"🔗 GENERATED INVITE LINK: {invite_link}")

        subject = f"Invitation to join {org_name} on {settings.PROJECT_NAME}"

        # Customize messaging based on whether user already has an account
        if user_exists:
            action_text = "Sign in to accept the invitation and join the organization:"
            button_text = "Sign In & Accept"
        else:
            action_text = "Click the button below to create your account and join the organization:"
            button_text = "Accept Invitation"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9fafb;">
            <div style="background-color: white; border-radius: 12px; padding: 40px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <div style="text-align: center; margin-bottom: 30px;">
                    <div style="display: inline-block; background-color: #4f46e5; padding: 12px; border-radius: 12px;">
                        <span style="color: white; font-size: 24px; font-weight: bold;">S</span>
                    </div>
                </div>
                
                <h1 style="font-size: 24px; font-weight: 600; color: #111827; margin-bottom: 16px; text-align: center;">
                    You've been invited!
                </h1>
                
                <p style="color: #374151; font-size: 16px; line-height: 24px; margin-bottom: 24px;">
                    <strong>{inviter_name}</strong> has invited you to join <strong>{org_name}</strong> on {settings.PROJECT_NAME}.
                </p>
                
                {f'<div style="background-color: #f3f4f6; border-left: 4px solid #4f46e5; padding: 16px; margin-bottom: 24px; border-radius: 4px;"><p style="color: #374151; margin: 0; font-style: italic;">"{message}"</p></div>' if message else ''}
                
                <p style="color: #374151; font-size: 16px; line-height: 24px; margin-bottom: 24px;">
                    {action_text}
                </p>
                
                <div style="text-align: center; margin: 32px 0;">
                    <a href="{invite_link}" style="display: inline-block; background-color: #4f46e5; color: white; padding: 14px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                        {button_text}
                    </a>
                </div>
                
                <p style="color: #6b7280; font-size: 14px; margin-bottom: 8px;">
                    Or copy and paste this link into your browser:
                </p>
                <p style="word-break: break-all; color: #4f46e5; font-size: 14px; margin-bottom: 24px;">
                    <a href="{invite_link}" style="color: #4f46e5;">{invite_link}</a>
                </p>
                
                <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 32px 0;">
                
                <p style="color: #9ca3af; font-size: 12px; text-align: center;">
                    This invitation will expire in {settings.INVITATION_EXPIRY_DAYS} days.<br>
                    If you didn't expect this invitation, you can safely ignore this email.
                </p>
            </div>
            
            <p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 24px;">
                © 2026 {settings.PROJECT_NAME}. All rights reserved.
            </p>
        </body>
        </html>
        """

        self.send_email(to_email, subject, html_content)

    def send_welcome_email(self, to_email: str, full_name: str):
        """Send a welcome email to a newly registered user."""
        login_link = f"{settings.FRONTEND_URL}/login"

        logger.info(f"📧 Sending welcome email to {to_email}")

        subject = f"Welcome to {settings.PROJECT_NAME}!"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9fafb;">
            <div style="background-color: white; border-radius: 12px; padding: 40px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <div style="text-align: center; margin-bottom: 30px;">
                    <div style="display: inline-block; background-color: #4f46e5; padding: 12px; border-radius: 12px;">
                        <span style="color: white; font-size: 24px; font-weight: bold;">S</span>
                    </div>
                </div>
                
                <h1 style="font-size: 24px; font-weight: 600; color: #111827; margin-bottom: 16px; text-align: center;">
                    Welcome to {settings.PROJECT_NAME}! 🎉
                </h1>
                
                <p style="color: #374151; font-size: 16px; line-height: 24px; margin-bottom: 24px;">
                    Hi <strong>{full_name}</strong>,
                </p>
                
                <p style="color: #374151; font-size: 16px; line-height: 24px; margin-bottom: 24px;">
                    Thank you for signing up! We're excited to have you on board.
                </p>
                
                <p style="color: #374151; font-size: 16px; line-height: 24px; margin-bottom: 24px;">
                    With {settings.PROJECT_NAME}, you can:
                </p>
                
                <ul style="color: #374151; font-size: 16px; line-height: 28px; margin-bottom: 24px; padding-left: 24px;">
                    <li>Upload and manage documents securely</li>
                    <li>Chat with AI to get insights from your documents</li>
                    <li>Collaborate with your team in organizations</li>
                </ul>
                
                <div style="text-align: center; margin: 32px 0;">
                    <a href="{login_link}" style="display: inline-block; background-color: #4f46e5; color: white; padding: 14px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                        Get Started
                    </a>
                </div>
                
                <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 32px 0;">
                
                <p style="color: #9ca3af; font-size: 12px; text-align: center;">
                    If you have any questions, feel free to reach out to our support team.<br>
                    We're here to help!
                </p>
            </div>
            
            <p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 24px;">
                © 2026 {settings.PROJECT_NAME}. All rights reserved.
            </p>
        </body>
        </html>
        """

        self.send_email(to_email, subject, html_content)

    def send_organization_approved_email(
        self,
        to_email: str,
        user_name: str,
        org_name: str,
    ):
        """Send an email notification when an organization request is approved."""
        dashboard_link = f"{settings.FRONTEND_URL}/dashboard"

        logger.info(f"📧 Sending organization approval email to {to_email} for org: {org_name}")

        subject = f"Your organization '{org_name}' has been approved!"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9fafb;">
            <div style="background-color: white; border-radius: 12px; padding: 40px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <div style="text-align: center; margin-bottom: 30px;">
                    <div style="display: inline-block; background-color: #4f46e5; padding: 12px; border-radius: 12px;">
                        <span style="color: white; font-size: 24px; font-weight: bold;">S</span>
                    </div>
                </div>
                
                <h1 style="font-size: 24px; font-weight: 600; color: #111827; margin-bottom: 16px; text-align: center;">
                    Congratulations! 🎉
                </h1>
                
                <p style="color: #374151; font-size: 16px; line-height: 24px; margin-bottom: 24px;">
                    Hi <strong>{user_name}</strong>,
                </p>
                
                <p style="color: #374151; font-size: 16px; line-height: 24px; margin-bottom: 24px;">
                    Great news! Your organization <strong>{org_name}</strong> has been approved and is now active on {settings.PROJECT_NAME}.
                </p>
                
                <div style="background-color: #f0fdf4; border-left: 4px solid #22c55e; padding: 16px; margin-bottom: 24px; border-radius: 4px;">
                    <p style="color: #166534; margin: 0; font-weight: 600;">What's included:</p>
                    <ul style="color: #166534; margin: 8px 0 0 0; padding-left: 20px;">
                        <li>30-day free trial</li>
                        <li>Full access to all features</li>
                        <li>Invite unlimited team members</li>
                    </ul>
                </div>
                
                <p style="color: #374151; font-size: 16px; line-height: 24px; margin-bottom: 24px;">
                    You can now start inviting team members, uploading documents, and using AI-powered features.
                </p>
                
                <div style="text-align: center; margin: 32px 0;">
                    <a href="{dashboard_link}" style="display: inline-block; background-color: #4f46e5; color: white; padding: 14px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                        Go to Dashboard
                    </a>
                </div>
                
                <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 32px 0;">
                
                <p style="color: #9ca3af; font-size: 12px; text-align: center;">
                    If you have any questions, feel free to reach out to our support team.<br>
                    We're here to help!
                </p>
            </div>
            
            <p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 24px;">
                © 2026 {settings.PROJECT_NAME}. All rights reserved.
            </p>
        </body>
        </html>
        """

        self.send_email(to_email, subject, html_content)


email_service = EmailService()
