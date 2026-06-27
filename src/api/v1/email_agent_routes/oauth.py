"""
Email Agent — Google OAuth connect flow.

  GET /oauth/google/start     (authenticated)  -> returns Google consent URL
  GET /oauth/google/callback  (public)         -> exchanges code, stores account

The user is identified across the redirect via a one-time `state` token stored
in Redis (state -> user_id), since the callback carries no Authorization header.
"""
import logging
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from src.config import settings
from src.core.cache import cache
from src.core.dependencies import get_current_user_id
from src.core.exceptions import AppException
from src.email_agent import accounts, google_oauth
from src.models.email_agent import ConnectStartResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_STATE_PREFIX = "email_agent:oauth_state:"


def _resolve_frontend_origin(request: Request) -> str:
    """
    Determine the frontend origin to return the user to after OAuth.

    Uses the request's Origin/Referer (so a localhost dev session returns to
    localhost and prod returns to prod), but only if it's in the CORS
    allowlist — otherwise falls back to the configured FRONTEND_URL. This
    prevents the callback from being abused as an open redirect.
    """
    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        if referer:
            # Strip path from referer to get the bare origin.
            parts = referer.split("/")
            if len(parts) >= 3:
                origin = f"{parts[0]}//{parts[2]}"
    if origin and origin.rstrip("/") in {o.rstrip("/") for o in settings.BACKEND_CORS_ORIGINS}:
        return origin.rstrip("/")
    return settings.FRONTEND_URL.rstrip("/")


@router.get(
    "/oauth/google/start",
    response_model=ConnectStartResponse,
    summary="Start Gmail connection",
)
async def google_oauth_start(
    request: Request,
    user_id: UUID = Depends(get_current_user_id),
) -> ConnectStartResponse:
    """Generate a Google consent URL for the authenticated user to connect Gmail."""
    state = secrets.token_urlsafe(32)
    await cache.set(
        f"{_STATE_PREFIX}{state}",
        {"user_id": str(user_id), "origin": _resolve_frontend_origin(request)},
        ttl=settings.EMAIL_AGENT_OAUTH_STATE_TTL,
    )
    url = google_oauth.build_authorization_url(state)
    return ConnectStartResponse(authorization_url=url)


@router.get(
    "/oauth/google/callback",
    summary="Gmail OAuth callback",
    include_in_schema=False,
)
async def google_oauth_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """Handle Google's redirect: validate state, exchange code, store the account."""
    # Email configuration lives in the admin panel, so return the user there.
    fallback_base = f"{settings.FRONTEND_URL.rstrip('/')}/admin"

    if error:
        logger.warning("Google OAuth returned error: %s", error)
        return RedirectResponse(f"{fallback_base}?connected=google&status=denied")

    if not code or not state:
        return RedirectResponse(f"{fallback_base}?connected=google&status=invalid")

    cached = await cache.get(f"{_STATE_PREFIX}{state}")
    if not cached:
        return RedirectResponse(f"{fallback_base}?connected=google&status=expired")
    await cache.delete(f"{_STATE_PREFIX}{state}")

    # Return the user to the same frontend they started from (admin panel).
    redirect_base = f"{cached.get('origin', settings.FRONTEND_URL).rstrip('/')}/admin"
    user_id = UUID(cached["user_id"])
    try:
        tokens = await google_oauth.exchange_code(code)
        await accounts.save_google_account(user_id, tokens)
    except AppException as e:
        logger.error("Gmail connect failed for user %s: %s", user_id, e.message)
        return RedirectResponse(f"{redirect_base}?connected=google&status=error")

    return RedirectResponse(f"{redirect_base}?connected=google&status=success")
