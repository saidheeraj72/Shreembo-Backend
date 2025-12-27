"""
Database connection and Supabase client management.
"""
from supabase import create_client, Client
from src.config import settings


class DatabaseClient:
    """Supabase database client wrapper."""

    def __init__(self):
        self._admin_client: Client | None = None
        self._anon_client: Client | None = None

    @property
    def admin(self) -> Client:
        """
        Get Supabase admin client (service role).
        Bypasses Row Level Security - use for backend operations.
        """
        if self._admin_client is None:
            self._admin_client = create_client(
                supabase_url=settings.SUPABASE_URL,
                supabase_key=settings.SUPABASE_SERVICE_ROLE_KEY,
            )
        return self._admin_client

    @property
    def anon(self) -> Client:
        """
        Get Supabase anonymous client (anon key).
        Respects Row Level Security - use for user-scoped operations.
        """
        if self._anon_client is None:
            self._anon_client = create_client(
                supabase_url=settings.SUPABASE_URL,
                supabase_key=settings.SUPABASE_ANON_KEY,
            )
        return self._anon_client

    def with_user_token(self, access_token: str) -> Client:
        """
        Create a client with user's JWT token for RLS.

        Args:
            access_token: User's JWT access token

        Returns:
            Supabase client with user context
        """
        client = create_client(
            supabase_url=settings.SUPABASE_URL,
            supabase_key=settings.SUPABASE_ANON_KEY,
        )
        # Set the user's JWT token
        client.auth.set_session(access_token, "")
        return client


# Global database client instance
db = DatabaseClient()
