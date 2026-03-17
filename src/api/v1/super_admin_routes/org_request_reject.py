from fastapi import APIRouter, Depends, HTTPException

from src.api.deps.permissions import require_super_admin

router = APIRouter()

@router.post("/organization-requests/{request_id}/reject")
async def reject_organization_request(
    request_id: str,
    rejection_reason: str,
    _: dict = Depends(require_super_admin),
):
    """
    Reject an organization request.

    **Requires:** Super admin access

    Body:
    - **rejection_reason**: Reason for rejection
    """
    from src.core.database import db
    from datetime import datetime

    # Get the request
    request_response = (
        db.admin.table("organization_requests")
        .select("*")
        .eq("id", request_id)
        .single()
        .execute()
    )

    if not request_response.data:
        raise HTTPException(status_code=404, detail="Organization request not found")

    request_data = request_response.data

    if request_data["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Request already {request_data['status']}"
        )

    # Update request status
    db.admin.table("organization_requests").update({
        "status": "rejected",
        "rejection_reason": rejection_reason,
        "reviewed_at": datetime.utcnow().isoformat(),
    }).eq("id", request_id).execute()

    return {
        "message": "Organization request rejected",
        "request_id": request_id,
    }
