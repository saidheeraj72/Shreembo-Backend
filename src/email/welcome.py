"""Auto-split email service part."""
import resend
from typing import Optional
import logging

from src.config import settings

logger = logging.getLogger(__name__)


class EmailWelcomeMixin:
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

