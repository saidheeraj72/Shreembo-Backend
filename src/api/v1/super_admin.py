"""Super Admin API router composition."""
from fastapi import APIRouter

from src.api.v1.super_admin_routes import (
    access,
    dashboard,
    platform,
    org_requests,
    org_request_approve,
    org_request_reject,
)

router = APIRouter()
router.include_router(access.router)
router.include_router(dashboard.router)
router.include_router(platform.router)
router.include_router(org_requests.router)
router.include_router(org_request_approve.router)
router.include_router(org_request_reject.router)
