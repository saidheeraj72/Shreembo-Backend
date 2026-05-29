"""
Symmetric encryption for OAuth tokens stored at rest.

Uses Fernet (AES-128-CBC + HMAC). The key is taken from
EMAIL_AGENT_ENCRYPTION_KEY if set, otherwise derived deterministically
from SUPABASE_JWT_SECRET so the feature works without extra config.
"""
import base64
import hashlib

from cryptography.fernet import Fernet

from src.config import settings


def _derive_key() -> bytes:
    """Return a urlsafe-base64 32-byte Fernet key."""
    configured = settings.EMAIL_AGENT_ENCRYPTION_KEY
    if configured:
        # Accept a raw Fernet key directly.
        return configured.encode()
    # Derive a stable 32-byte key from the JWT secret.
    digest = hashlib.sha256(settings.SUPABASE_JWT_SECRET.encode()).digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_derive_key())


def encrypt(plaintext: str) -> str:
    """Encrypt a string, returning a urlsafe token."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by encrypt()."""
    return _fernet.decrypt(token.encode()).decode()
