from fastapi import Header, HTTPException, Depends
from src.database.session import get_supabase_client, get_service_role_client
from src.services.super_admin_service import super_admin_service
from typing import Tuple, Optional
from uuid import UUID

def get_current_user_id(authorization: str = Header(None)) -> UUID:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    token = authorization.replace("Bearer ", "")
    supabase = get_supabase_client() # Use public client for auth
    
    try:
        user_response = supabase.auth.get_user(token)
        return UUID(str(user_response.user.id))
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

def get_current_org_context(user_id: UUID = Depends(get_current_user_id)) -> Tuple[UUID, Optional[UUID]]:
    """
    Retrieves the user's ID and associated organization ID from their profile.
    """
    supabase = get_service_role_client() # Use service role to bypass RLS for profile lookup
    try:
        profile_res = supabase.table("profiles").select("org_id").eq("id", user_id).single().execute()
        org_id = UUID(str(profile_res.data["org_id"])) if profile_res.data and profile_res.data["org_id"] else None
        return user_id, org_id
    except Exception as e:
        # User might exist but have no profile or org_id
        return user_id, None

def verify_organization_admin(org_context: Tuple[UUID, Optional[UUID]] = Depends(get_current_org_context)):
    user_id, org_id = org_context
    if not org_id:
        raise HTTPException(status_code=403, detail="User is not part of any organization.")

    supabase = get_service_role_client()
    try:
        # Check if user has an 'Owner' or 'Admin' role for this organization
        # First, get the role IDs for 'Owner' and 'Admin' for this org
        roles_res = supabase.table("roles").select("id, name").eq("org_id", org_id).in_("name", ["Owner", "Admin"]).execute()
        admin_role_ids = [r["id"] for r in roles_res.data]

        if not admin_role_ids:
            raise HTTPException(status_code=403, detail="No admin roles defined for this organization.")

        # Then, check if the user is a member with one of these roles
        member_res = supabase.table("organization_members").select("id").eq("org_id", org_id).eq("user_id", user_id).in_("role_id", admin_role_ids).execute()
        
        if not member_res.data:
            raise HTTPException(status_code=403, detail="User does not have admin privileges for this organization.")
            
        return org_id # Return the org_id to the route handler
            
    except Exception as e:
        print(f"Error in verify_organization_admin: {e}") # Debugging
        raise HTTPException(status_code=500, detail="Internal server error during authorization.")

def get_current_user_email(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    token = authorization.replace("Bearer ", "")
    supabase = get_supabase_client()
    
    try:
        user = supabase.auth.get_user(token)
        return user.user.email
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")

def verify_super_admin(email: str = Depends(get_current_user_email)):
    if not super_admin_service.is_super_admin(email):
        raise HTTPException(status_code=403, detail="Not authorized as Super Admin")
    return email
