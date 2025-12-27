from supabase import create_client, Client
from src.config import settings

def get_supabase_client() -> Client:
    """
    Returns a Supabase client authenticated with the Anon Key.
    Use this for operations that should respect RLS (e.g. public data).
    """
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)

def get_service_role_client() -> Client:
    """
    Returns a Supabase client authenticated with the Service Role Key.
    Use this for administrative tasks (e.g. creating users, bypassing RLS).
    WARNING: This bypasses all Row Level Security.
    """
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)