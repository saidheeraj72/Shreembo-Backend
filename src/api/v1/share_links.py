"""Share link API routes."""
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends

from src.core.dependencies import get_current_user_id, get_current_org_context
from src.chat.share_link import share_link_service
from src.models.share_link import ShareLinkCreate, ShareLinkResponse, ShareLinkAccess

router = APIRouter()


@router.post("", response_model=ShareLinkResponse)
async def create_share_link(
    data: ShareLinkCreate,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context)
):
    """Create a share link for a document or folder."""
    return await share_link_service.create(
        node_id=data.node_id,
        org_id=UUID(org_context["org_id"]),
        created_by=user_id,
        permission=data.permission.value,
        password=data.password,
        expires_in_days=data.expires_in_days,
        max_access_count=data.max_access_count,
        name=data.name
    )


@router.get("/node/{node_id}", response_model=List[ShareLinkResponse])
async def list_share_links(
    node_id: UUID,
    org_context: dict = Depends(get_current_org_context)
):
    """List share links for a node."""
    return await share_link_service.list_for_node(node_id, UUID(org_context["org_id"]))


@router.delete("/{link_id}")
async def revoke_share_link(
    link_id: UUID,
    org_context: dict = Depends(get_current_org_context)
):
    """Revoke a share link."""
    await share_link_service.revoke(link_id, UUID(org_context["org_id"]))
    return {"success": True}


@router.get("/access/{token}")
async def access_share_link(token: str):
    """Access content via share link (public endpoint)."""
    return await share_link_service.validate_access(token)


@router.post("/access/{token}")
async def access_share_link_with_password(token: str, data: ShareLinkAccess):
    """Access content via password-protected share link."""
    return await share_link_service.validate_access(token, data.password)
