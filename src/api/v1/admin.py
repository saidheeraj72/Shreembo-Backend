"""Organization Admin API router composition."""
from fastapi import APIRouter

from src.api.v1.admin_routes import (
    audit,
    branches_crud,
    users_management,
    user_permissions,
    invitations,
    roles,
    groups_crud,
    group_members,
    folder_access,
    init,
)

router = APIRouter()
router.include_router(init.router)
router.include_router(audit.router)
router.include_router(branches_crud.router)
router.include_router(users_management.router)
router.include_router(user_permissions.router)
router.include_router(invitations.router)
router.include_router(roles.router)
router.include_router(groups_crud.router)
router.include_router(group_members.router)
router.include_router(folder_access.router)
