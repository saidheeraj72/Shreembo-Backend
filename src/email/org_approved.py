"""Auto-split email service part."""
import resend
from typing import Optional
import logging

from src.config import settings

logger = logging.getLogger(__name__)


class EmailOrgApprovedMixin:
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
