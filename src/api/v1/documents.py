"""Document API router composition."""
from fastapi import APIRouter

from src.api.v1.document_routes import folders, upload, documents, search_ws

router = APIRouter()
router.include_router(folders.router)
router.include_router(upload.router)
router.include_router(documents.router)
router.include_router(search_ws.router)
