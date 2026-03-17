from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query

from src.core.websocket import ws_manager
from src.core.database import db
from src.core.dependencies import get_current_user_id, get_current_org_context
from src.access.permission import permission_service
from src.models.document import SearchResult

router = APIRouter()

# Search
@router.get("/search", response_model=List[SearchResult])
async def search_documents(
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, ge=1, le=50),
    folder_id: Optional[UUID] = Query(None),
    org_context: dict = Depends(get_current_org_context),
    user_id: UUID = Depends(get_current_user_id)
):
    """Search documents by name, filtered by user access permissions."""
    org_id = org_context.get("org_id")

    query = (
        db.admin.table("storage_nodes")
        .select("*")
        .eq("node_type", "file")
        .eq("status", "active")
        .ilike("name", f"%{q}%")
        .limit(top_k)
    )

    if org_id:
        query = query.eq("org_id", org_id)
    else:
        query = query.is_("org_id", "null").eq("owner_id", str(user_id))

    if folder_id:
        query = query.eq("parent_id", str(folder_id))

    result = query.order("name").execute()
    documents = result.data or []

    # Apply access filtering for org users
    if org_id and user_id:
        is_admin = await permission_service.is_admin_or_owner(user_id, org_id)
        if not is_admin:
            accessible_ids = await permission_service.get_accessible_folder_ids(user_id, org_id)
            if not accessible_ids:
                return []
            documents = [
                d for d in documents
                if d.get("parent_id") in accessible_ids or d["id"] in accessible_ids
            ]

    # Wrap in SearchResult format for frontend compatibility
    return [
        {"document": doc, "score": 1.0, "chunk_text": None}
        for doc in documents
    ]


# WebSocket for upload progress
@router.websocket("/ws/{user_id}")
async def websocket_upload_progress(websocket: WebSocket, user_id: str):
    """WebSocket for real-time upload progress."""
    await ws_manager.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, user_id)
