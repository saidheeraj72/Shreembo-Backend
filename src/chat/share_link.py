"""Share link service."""
import hashlib
import secrets
from typing import Optional, List
from uuid import UUID
from datetime import datetime, timedelta

from src.core.database import db
from src.core.exceptions import NotFoundError, AuthorizationError


class ShareLinkService:
    @staticmethod
    async def create(node_id: UUID, org_id: UUID, created_by: UUID,
                     permission: str = "view", password: str = None,
                     expires_in_days: int = None, max_access_count: int = None,
                     name: str = None) -> dict:
        """Create a share link."""
        token = secrets.token_urlsafe(32)
        password_hash = hashlib.sha256(password.encode()).hexdigest() if password else None
        expires_at = (datetime.utcnow() + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None

        data = {
            "node_id": str(node_id),
            "org_id": str(org_id),
            "token": token,
            "permission": permission,
            "password_hash": password_hash,
            "expires_at": expires_at,
            "max_access_count": max_access_count,
            "name": name,
            "created_by": str(created_by),
        }

        result = db.admin.table("share_links").insert(data).execute()
        link = result.data[0] if result.data else None

        if link:
            link["url"] = f"/share/{token}"
            link["has_password"] = password_hash is not None

        return link

    @staticmethod
    async def get_by_token(token: str) -> Optional[dict]:
        """Get share link by token."""
        result = db.admin.table("share_links").select(
            "*, storage_nodes(*)"
        ).eq("token", token).maybe_single().execute()
        return result.data

    @staticmethod
    async def validate_access(token: str, password: str = None) -> dict:
        """Validate share link access."""
        link = await ShareLinkService.get_by_token(token)

        if not link:
            raise NotFoundError("Share link not found")

        if not link.get("is_active"):
            raise AuthorizationError("This link has been disabled")

        # Check expiration
        if link.get("expires_at"):
            if datetime.fromisoformat(link["expires_at"].replace("Z", "")) < datetime.utcnow():
                raise AuthorizationError("This link has expired")

        # Check access count
        if link.get("max_access_count"):
            if link.get("access_count", 0) >= link["max_access_count"]:
                raise AuthorizationError("This link has reached its access limit")

        # Check password
        if link.get("password_hash"):
            if not password:
                raise AuthorizationError("Password required", detail={"requires_password": True})
            if hashlib.sha256(password.encode()).hexdigest() != link["password_hash"]:
                raise AuthorizationError("Invalid password")

        # Update access count
        db.admin.table("share_links").update({
            "access_count": link.get("access_count", 0) + 1,
            "last_accessed_at": datetime.utcnow().isoformat()
        }).eq("id", link["id"]).execute()

        return {
            "permission": link["permission"],
            "node": link.get("storage_nodes"),
        }

    @staticmethod
    async def list_for_node(node_id: UUID, org_id: UUID) -> List[dict]:
        """List share links for a node."""
        result = db.admin.table("share_links").select("*").eq(
            "node_id", str(node_id)
        ).eq("org_id", str(org_id)).order("created_at", desc=True).execute()

        links = result.data or []
        for link in links:
            link["url"] = f"/share/{link['token']}"
            link["has_password"] = link.get("password_hash") is not None

        return links

    @staticmethod
    async def revoke(link_id: UUID, org_id: UUID) -> bool:
        """Revoke a share link."""
        db.admin.table("share_links").update({
            "is_active": False
        }).eq("id", str(link_id)).eq("org_id", str(org_id)).execute()
        return True


share_link_service = ShareLinkService()
