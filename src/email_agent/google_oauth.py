"""
Google OAuth2 helper for the email agent.

Handles the authorization-code flow against Google's OAuth endpoints
using httpx (no heavy google client libraries).
"""
import logging
from urllib.parse import urlencode

import httpx

from src.config import settings
from src.core.exceptions import AppException

logger = logging.getLogger(__name__)

AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def build_authorization_url(state: str) -> str:
    """Build the Google consent-screen URL the user is redirected to."""
    if not settings.GMAIL_CLIENT_ID:
        raise AppException("Gmail is not configured (missing GMAIL_CLIENT_ID).", status_code=503)

    params = {
        "client_id": settings.GMAIL_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(settings.GMAIL_SCOPES),
        "access_type": "offline",      # request a refresh token
        "prompt": "consent",           # force refresh token issuance
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_BASE_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """
    Exchange an authorization code for tokens.

    Returns the raw token response:
        access_token, refresh_token, expires_in, scope, token_type
    """
    data = {
        "code": code,
        "client_id": settings.GMAIL_CLIENT_ID,
        "client_secret": settings.GMAIL_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(TOKEN_URL, data=data)
    if resp.status_code != 200:
        logger.error("Google token exchange failed: %s", resp.text)
        raise AppException("Failed to exchange authorization code with Google.", status_code=502)
    return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """
    Use a refresh token to obtain a new access token.

    Returns: access_token, expires_in, scope, token_type
    (Google does not return a new refresh_token here.)
    """
    data = {
        "refresh_token": refresh_token,
        "client_id": settings.GMAIL_CLIENT_ID,
        "client_secret": settings.GMAIL_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(TOKEN_URL, data=data)
    if resp.status_code != 200:
        logger.error("Google token refresh failed: %s", resp.text)
        raise AppException("Failed to refresh Google access token. Reconnect the account.", status_code=401)
    return resp.json()


async def get_userinfo(access_token: str) -> dict:
    """Fetch the connected account's profile (email, id)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        logger.error("Google userinfo failed: %s", resp.text)
        raise AppException("Failed to fetch Google account info.", status_code=502)
    return resp.json()


async def revoke_token(token: str) -> None:
    """Best-effort revoke of a token at Google."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(REVOKE_URL, params={"token": token})
    except Exception as e:  # noqa: BLE001 - revoke is best-effort
        logger.warning("Google token revoke failed (ignored): %s", e)
