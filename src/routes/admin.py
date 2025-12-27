from fastapi import APIRouter, Depends, HTTPException
from src.dependencies import verify_organization_admin, get_current_org_context
from src.services.admin_service import admin_service
from typing import Tuple, Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr

router = APIRouter(dependencies=[Depends(verify_organization_admin)])

# Pydantic models for request bodies
class BranchCreate(BaseModel):
    name: str
    location: Optional[str] = None

class MemberInvite(BaseModel):
    email: EmailStr
    full_name: str
    role_name: str = "Member"

class MemberRoleUpdate(BaseModel):
    new_role_name: str

class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    color: str = "primary"
    permissions: Optional[dict] = None

class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    permissions: Optional[dict] = None

class MemberPermissionsUpdate(BaseModel):
    permissions: dict  # {"documents": {"view": true, ...}, ...}

# --- Branch Management ---
@router.get("/branches")
def list_branches(org_id: UUID = Depends(verify_organization_admin)):
    """
    List all branches for the current organization.
    Requires organization admin privileges.
    """
    return admin_service.list_branches(org_id)

@router.post("/branches")
def create_branch(branch_data: BranchCreate, org_id: UUID = Depends(verify_organization_admin)):
    """
    Create a new branch for the current organization.
    Requires organization admin privileges.
    """
    return admin_service.create_branch(org_id, branch_data.name, branch_data.location)

# --- User/Member Management ---
@router.get("/members")
def list_organization_members(org_id: UUID = Depends(verify_organization_admin)):
    """
    List all members in the current organization with their roles.
    Requires organization admin privileges.
    """
    return admin_service.list_org_members(org_id)

@router.post("/members/invite")
def invite_organization_member(member_data: MemberInvite, org_id: UUID = Depends(verify_organization_admin)):
    """
    Invite a new user to the organization.
    Requires organization admin privileges.
    """
    return admin_service.invite_member(org_id, member_data.email, member_data.full_name, member_data.role_name)

@router.put("/members/{user_id}/role")
def update_member_role(user_id: UUID, role_update: MemberRoleUpdate, org_id: UUID = Depends(verify_organization_admin)):
    """
    Update a member's role within the organization.
    Requires organization admin privileges.
    """
    return admin_service.update_member_role(org_id, user_id, role_update.new_role_name)

@router.get("/members/{user_id}/permissions")
def get_member_permissions(user_id: UUID, org_id: UUID = Depends(verify_organization_admin)):
    """
    Get a member's custom permissions (overrides beyond their role).
    Requires organization admin privileges.
    """
    return admin_service.get_member_permissions(org_id, user_id)

@router.put("/members/{user_id}/permissions")
def update_member_permissions(user_id: UUID, perm_update: MemberPermissionsUpdate, org_id: UUID = Depends(verify_organization_admin)):
    """
    Update a member's custom permissions.
    Requires organization admin privileges.
    """
    return admin_service.update_member_permissions(org_id, user_id, perm_update.permissions)

@router.delete("/members/{user_id}")
def remove_member(user_id: UUID, org_id: UUID = Depends(verify_organization_admin)):
    """
    Remove a user from the organization.
    Requires organization admin privileges.
    """
    return admin_service.remove_member(org_id, user_id)

# --- Role Management ---
@router.get("/roles")
def list_organization_roles(org_id: UUID = Depends(verify_organization_admin)):
    """
    List all custom roles defined for the current organization.
    Requires organization admin privileges.
    """
    return admin_service.list_roles(org_id)

@router.post("/roles")
def create_organization_role(role_data: RoleCreate, org_id: UUID = Depends(verify_organization_admin)):
    """
    Create a new custom role for the organization.
    Requires organization admin privileges.
    """
    return admin_service.create_role(org_id, role_data.name, role_data.description, role_data.color, role_data.permissions)

@router.put("/roles/{role_id}")
def update_organization_role(role_id: UUID, role_data: RoleUpdate, org_id: UUID = Depends(verify_organization_admin)):
    """
    Update an existing role in the organization.
    Requires organization admin privileges.
    """
    return admin_service.update_role(org_id, role_id, role_data.name, role_data.description, role_data.color, role_data.permissions)

@router.delete("/roles/{role_id}")
def delete_organization_role(role_id: UUID, org_id: UUID = Depends(verify_organization_admin)):
    """
    Delete a role from the organization.
    Requires organization admin privileges.
    """
    return admin_service.delete_role(org_id, role_id)
