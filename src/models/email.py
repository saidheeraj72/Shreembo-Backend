"""
Pydantic models for Email Writer module.
"""
from typing import Optional
from enum import Enum
from pydantic import BaseModel, Field


class EmailTone(str, Enum):
    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    FORMAL = "formal"
    CASUAL = "casual"
    PERSUASIVE = "persuasive"
    APOLOGETIC = "apologetic"


class EmailType(str, Enum):
    NEW = "new"
    REPLY = "reply"
    FOLLOW_UP = "follow-up"
    THANK_YOU = "thank-you"
    INTRODUCTION = "introduction"
    REQUEST = "request"


class EmailGenerateRequest(BaseModel):
    """Request body for email generation."""
    key_points: str = Field(..., min_length=1, max_length=20000, description="Main points to include in the email")
    subject: Optional[str] = Field(None, max_length=200, description="Email subject line")
    recipient_context: Optional[str] = Field(None, max_length=200, description="Who the email is for (e.g., Client, Manager)")
    tone: EmailTone = Field(default=EmailTone.PROFESSIONAL, description="Desired tone of the email")
    email_type: EmailType = Field(default=EmailType.NEW, description="Type of email to generate")


class EmailGenerateResponse(BaseModel):
    """Response body for email generation."""
    generated_email: str
    subject: Optional[str] = None
