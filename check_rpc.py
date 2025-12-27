from src.database.session import get_service_role_client

def check_rpc():
    supabase = get_service_role_client()
    try:
        # Try to call a non-existent function or a common one
        res = supabase.rpc('version', {}).execute()
        print("RPC 'version' exists:", res)
    except Exception as e:
        print("RPC check failed:", e)

if __name__ == "__main__":
    check_rpc()
