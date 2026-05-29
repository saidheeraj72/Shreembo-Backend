"""
Connected mailbox storage and access-token management.

Persists rows in `email_accounts`, encrypting OAuth tokens at rest, and
mints/refreshes Gmail access tokens on demand.
"""
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.core.database import db
from src.core.exceptions import AppException, NotFoundError
from src.email_agent import crypto, google_oauth

logger = logging.getLogger(__name__)

# Refresh access tokens slightly before they actually expire.
_EXPIRY_SKEW = timedelta(seconds=60)


async def save_google_account(user_id: UUID, tokens: dict) -> dict:
    """
    Persist (or update) a Google account from a token-exchange response.

    Upserts on (user_id, provider, email_address).
    """
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        # Google omits refresh_token if the user previously consented and we
        # didn't force prompt=consent. We always force it, so this is unexpected.
        raise AppException(
            "Google did not return a refresh token. Disconnect the app in your "
            "Google account permissions and reconnect.",
            status_code=400,
        )

    userinfo = await google_oauth.get_userinfo(access_token)
    email_address = userinfo.get("email")
    if not email_address:
        raise AppException("Could not determine the Google account email.", status_code=502)

    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=int(tokens.get("expires_in", 3600))
    )

    row = {
        "user_id": str(user_id),
        "provider": "google",
        "email_address": email_address,
        "refresh_token": crypto.encrypt(refresh_token),
        "access_token": crypto.encrypt(access_token),
        "token_expires_at": expires_at.isoformat(),
        "scopes": tokens.get("scope"),
        "status": "active",
    }

    result = (
        db.admin.table("email_accounts")
        .upsert(row, on_conflict="user_id,provider,email_address")
        .execute()
    )
    logger.info("Saved Google account %s for user %s", email_address, user_id)
    return result.data[0]


async def list_accounts(user_id: UUID) -> list[dict]:
    """Return the user's connected mailboxes (no token material)."""
    result = (
        db.admin.table("email_accounts")
        .select("id, provider, email_address, status, created_at, updated_at")
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


async def get_account(user_id: UUID, account_id: UUID) -> dict:
    """Fetch a single account row (with tokens) scoped to the user."""
    result = (
        db.admin.table("email_accounts")
        .select("*")
        .eq("id", str(account_id))
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise NotFoundError("Connected email account not found.")
    return result.data


async def delete_account(user_id: UUID, account_id: UUID) -> None:
    """Revoke tokens at the provider and delete the stored account."""
    account = await get_account(user_id, account_id)
    try:
        await google_oauth.revoke_token(crypto.decrypt(account["refresh_token"]))
    except Exception as e:  # noqa: BLE001 - deletion proceeds regardless
        logger.warning("Token revoke failed for account %s: %s", account_id, e)

    db.admin.table("email_accounts").delete().eq("id", str(account_id)).eq(
        "user_id", str(user_id)
    ).execute()
    logger.info("Deleted email account %s for user %s", account_id, user_id)


async def get_valid_access_token(account: dict) -> str:
    """
    Return a non-expired access token for the account, refreshing via the
    stored refresh token when necessary and persisting the new token.
    """
    expires_at = account.get("token_expires_at")
    access_token_enc = account.get("access_token")

    if access_token_enc and expires_at:
        expiry = datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) + _EXPIRY_SKEW < expiry:
            return crypto.decrypt(access_token_enc)

    # Refresh.
    refresh_token = crypto.decrypt(account["refresh_token"])
    tokens = await google_oauth.refresh_access_token(refresh_token)
    new_access = tokens["access_token"]
    new_expiry = datetime.now(timezone.utc) + timedelta(
        seconds=int(tokens.get("expires_in", 3600))
    )

    db.admin.table("email_accounts").update(
        {
            "access_token": crypto.encrypt(new_access),
            "token_expires_at": new_expiry.isoformat(),
            "status": "active",
        }
    ).eq("id", account["id"]).execute()

    return new_access
