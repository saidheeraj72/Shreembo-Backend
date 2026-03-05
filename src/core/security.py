"""
Security utilities for authentication and authorization.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from jose import JWTError, jwt, jwk
from passlib.context import CryptContext
from src.config import settings

logger = logging.getLogger(__name__)


# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash.

    Args:
        plain_password: Plain text password
        hashed_password: Hashed password

    Returns:
        True if password matches
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Hash a password.

    Args:
        password: Plain text password

    Returns:
        Hashed password
    """
    return pwd_context.hash(password)


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a JWT access token.

    Args:
        data: Payload data to encode
        expires_delta: Token expiration time

    Returns:
        Encoded JWT token
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    return encoded_jwt


def create_refresh_token(data: Dict[str, Any]) -> str:
    """
    Create a JWT refresh token.

    Args:
        data: Payload data to encode

    Returns:
        Encoded JWT refresh token
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    return encoded_jwt


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Decode and verify a JWT token.

    Args:
        token: JWT token to decode

    Returns:
        Decoded payload or None if invalid
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        return payload
    except JWTError:
        return None


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
    import secrets
    return secrets.token_urlsafe(32)
