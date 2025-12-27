from fastapi import APIRouter, HTTPException, Depends
from src.schemas.auth import OrgRequestUpdate
from src.services.super_admin_service import super_admin_service
from src.dependencies import verify_super_admin

router = APIRouter(dependencies=[Depends(verify_super_admin)])

@router.get("/requests")
def get_requests(status: str = None):
    """
    Super Admin: List organization requests. Optional status filter (pending, approved, rejected).
    """
    return super_admin_service.get_requests(status)

@router.get("/organizations")
def get_organizations():
    """
    Super Admin: List all active organizations.
    """
    return super_admin_service.get_all_organizations()

@router.get("/verify")
def verify_access():
    """
    Check if the current user has Super Admin access.
    """
    return {"is_super_admin": True}

@router.post("/requests/{request_id}/approve")
def approve_request(request_id: str):
    """
    Super Admin: Approve an Organization Request.
    - Creates the Organization
    - Links the Request User as Owner
    - Updates Request Status
    """
    return super_admin_service.approve_org_request(request_id)

@router.post("/requests/{request_id}/reject")
def reject_request(request_id: str, payload: OrgRequestUpdate):
    """
    Super Admin: Reject an Organization Request.
    """
    if not payload.rejection_reason:
         raise HTTPException(status_code=400, detail="Rejection reason required")
         
    return super_admin_service.reject_org_request(request_id, payload.rejection_reason)
