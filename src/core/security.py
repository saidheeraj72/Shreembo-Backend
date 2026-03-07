"""
Security utilities for authentication and authorization.
"""
import json
import logging
import secrets
from typing import Optional, Dict, Any
from jose import JWTError, jwt, jwk
from src.config import settings

logger = logging.getLogger(__name__)


def _get_supabase_es256_key():
    """Build the ES256 public key from the JWK in settings."""
    if not settings.SUPABASE_JWT_JWK:
        return None
    try:
        jwk_dict = json.loads(settings.SUPABASE_JWT_JWK)
        return jwk.construct(jwk_dict, algorithm="ES256")
    except Exception as e:
        logger.error("Failed to construct ES256 key from JWK: %s", e)
        return None


# Cache the key at module level
_es256_key = None


def _get_es256_key():
    global _es256_key
    if _es256_key is None:
        _es256_key = _get_supabase_es256_key()
    return _es256_key


def verify_supabase_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify Supabase JWT token using ES256 public key (JWK).

    Args:
        token: Supabase JWT token

    Returns:
        Decoded payload or None if invalid
    """
    es256_key = _get_es256_key()
    if not es256_key:
        logger.error("SUPABASE_JWT_JWK not configured, cannot verify tokens")
        return None

    try:
        payload = jwt.decode(
            token,
            key=es256_key,
            algorithms=["ES256"],
            options={
                "verify_aud": False,
            }
        )
        return payload
    except JWTError as e:
        logger.warning("JWT decode error: %s", e)
        return None


def generate_invite_token() -> str:
    """
    Generate a secure random invite token.

    Returns:
        Random token string
    """
    return secrets.token_urlsafe(32)
