from fastapi import APIRouter, HTTPException, Depends
from src.schemas.auth import UserSignup, OrgRegistration, UserLogin, Token
from src.services.auth_service import auth_service
from src.dependencies import get_current_org_context
from typing import Tuple, Optional
from uuid import UUID

router = APIRouter()

@router.post("/signup/personal", response_model=dict)
def signup_personal(data: UserSignup):
    """
    Direct signup for Personal Accounts.
    - Creates Supabase User
    - Creates Profile with 14-day expiry
    """
    return auth_service.signup_personal(data)

@router.post("/signup/organization", response_model=dict)
def register_organization(data: OrgRegistration):
    """
    Registration for New Organizations.
    - Creates Supabase User (if new)
    - Creates 'Organization Request' (Pending)
    - DOES NOT create Organization yet (Requires Super Admin Approval)
    """
    return auth_service.register_organization(data)

@router.post("/login", response_model=Token)
def login(data: UserLogin):
    """
    Standard Login. Returns Access Token + User Info.
    """
    return auth_service.login(data)

@router.get("/me", response_model=dict)
def get_current_user_profile_and_permissions(org_context: Tuple[UUID, Optional[UUID]] = Depends(get_current_org_context)):
    """
    Get current user's profile and effective permissions for their organization.
    """
    user_id, org_id = org_context
    # if not org_id:
    #     raise HTTPException(status_code=403, detail="User is not part of any organization.")
    
    # Fetch profile data
    profile_data = auth_service.get_user_profile(user_id)
    
    # Fetch and combine permissions
    # If org_id is None, pass a placeholder or handle in service. 
    # Passing "None" (string) might cause issues if uuid cast expected. 
    # But get_my_permissions takes string.
    org_id_str = str(org_id) if org_id else None
    permissions = auth_service.get_my_permissions(str(user_id), org_id_str, profile_data["email"])
    
    return {
        "user": profile_data,
        "permissions": permissions
    }
