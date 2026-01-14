"""
Authentication Pydantic models.
"""
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Login request model."""

    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    """Login response model."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


class SignupRequest(BaseModel):
    """Signup request model."""

    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str
    account_type: str = "personal"


class SignupResponse(BaseModel):
    """Signup response model."""

    user: dict
    message: str


class RefreshTokenRequest(BaseModel):
    """Refresh token request model."""

    refresh_token: str


class RefreshTokenResponse(BaseModel):
    """Refresh token response model."""

    access_token: str
    token_type: str = "bearer"


class PasswordResetRequest(BaseModel):
    """Password reset request model."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Password reset confirmation model."""

    token: str
    new_password: str = Field(..., min_length=8)


class ChangePasswordRequest(BaseModel):
    """Change password request model."""

    current_password: str
    new_password: str = Field(..., min_length=8)


class AcceptInvitationRequest(BaseModel):
    """Accept invitation request model."""

    token: str
    email: EmailStr
    password: Optional[str] = Field(None, min_length=8)
    full_name: Optional[str] = None


class VerifyInviteResponse(BaseModel):
    """Verify invite response model."""

    valid: bool
    user_exists: bool
    email: str
    org_name: str
    org_logo: Optional[str] = None
    inviter_name: Optional[str] = None
    expires_at: Optional[str] = None
    message: Optional[str] = None
