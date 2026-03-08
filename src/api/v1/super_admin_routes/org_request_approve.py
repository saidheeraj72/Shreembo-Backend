from fastapi import APIRouter, Depends

from src.api.deps.permissions import require_super_admin
from src.super_admin.request_approval import approve_organization_request_workflow

router = APIRouter()

@router.post("/organization-requests/{request_id}/approve")
async def approve_organization_request(
    request_id: str,
    _: dict = Depends(require_super_admin),
):
    return await approve_organization_request_workflow(request_id)
