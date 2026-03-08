from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query

from src.core.websocket import ws_manager
from src.core.dependencies import get_current_user_id, get_current_org_context
from src.llm.embedding import embedding_service
from src.models.document import SearchResult

router = APIRouter()

# Search
@router.get("/search", response_model=List[SearchResult])
async def search_documents(
    q: str = Query(..., min_length=1),
    top_k: int = Query(10, ge=1, le=50),
    folder_id: Optional[UUID] = Query(None),
    org_context: dict = Depends(get_current_org_context),
    user_id: UUID = Depends(get_current_user_id) # Inject user_id
):
    """Search documents using semantic search."""
    org_id = org_context.get("org_id")
    return await embedding_service.search(q, UUID(org_id) if org_id else None, user_id, top_k, folder_id)


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
