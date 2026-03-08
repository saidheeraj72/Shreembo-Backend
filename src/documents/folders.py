"""Auto-split document service part."""
from typing import Optional, List
from uuid import UUID, uuid4
from urllib.parse import quote
import zipfile
from io import BytesIO

from src.core.database import db
from src.core.s3 import s3_client
from src.core.exceptions import NotFoundError, ValidationError, ConflictError
from src.llm.embedding import embedding_service


class DocumentFoldersMixin:
    @staticmethod
    async def get_branches(org_id: Optional[UUID]) -> List[dict]:
        """Get active branches for organization."""
        if not org_id:
            return []
            
        result = db.admin.table("branches").select("*").eq(
            "org_id", str(org_id)
        ).eq("is_active", True).execute()
        
        # Add node_type='branch' to simulate folder structure for frontend
        branches = []
        for b in result.data:
            b["node_type"] = "branch"
            b["owner_id"] = str(uuid4()) # Placeholder owner
            branches.append(b)
        return branches

    @staticmethod
    async def create_folder(org_id: Optional[UUID], owner_id: UUID, name: str,
                           parent_id: Optional[UUID] = None, 
                           branch_id: Optional[UUID] = None,
                           description: str = None) -> dict:
        data = {
            "org_id": str(org_id) if org_id else None,
            "owner_id": str(owner_id),
            "name": name,
            "node_type": "folder",
            "description": description,
            "parent_id": str(parent_id) if parent_id else None,
            "branch_id": str(branch_id) if branch_id else None,
        }
        result = db.admin.table("storage_nodes").insert(data).execute()
        return result.data[0] if result.data else None

    @staticmethod
    async def get_folder(folder_id: UUID, org_id: Optional[UUID]) -> Optional[dict]:
        query = db.admin.table("storage_nodes").select("*").eq(
            "id", str(folder_id)
        ).eq("node_type", "folder")
        
        if org_id:
            query = query.eq("org_id", str(org_id))
        else:
            query = query.is_("org_id", "null")
            
        result = query.maybe_single().execute()
        return result.data

    @staticmethod
    async def get_folder_contents(org_id: Optional[UUID],
                                folder_id: Optional[UUID] = None,
                                branch_id: Optional[UUID] = None,
                                owner_id: Optional[UUID] = None,
                                user_id: Optional[UUID] = None) -> dict:
        """Get folder contents, filtered by user permissions if applicable."""
        from src.access.permission import permission_service

        query = db.admin.table("storage_nodes").select("*").eq("status", "active")

        if org_id:
            query = query.eq("org_id", str(org_id))
        else:
            query = query.is_("org_id", "null")
            if owner_id:
                query = query.eq("owner_id", str(owner_id))

        if folder_id:
            query = query.eq("parent_id", str(folder_id))
        elif branch_id:
            # Root of a branch
            query = query.eq("branch_id", str(branch_id)).is_("parent_id", "null")
        else:
            # Root (no parent)
            # For personal users (no org, no branch), this lists root files/folders
            query = query.is_("parent_id", "null").is_("branch_id", "null")

        result = query.order("node_type").order("name").execute()

        folders = [r for r in result.data if r["node_type"] == "folder"]
        documents = [r for r in result.data if r["node_type"] == "file"]

        # Apply folder access filtering for organization users
        if org_id and user_id:
            # Check if user is admin/owner (they see everything)
            is_admin = await permission_service.is_admin_or_owner(user_id, org_id)

            if not is_admin:
                # Get accessible folder IDs for this user
                accessible_ids = await permission_service.get_accessible_folder_ids(user_id, org_id)

                # If user has no folder permissions, they see nothing
                if not accessible_ids:
                    return {"folders": [], "documents": []}

                # Filter folders - only show folders user has access to
                folders = [f for f in folders if f["id"] in accessible_ids]

                # Filter documents - show docs in accessible folders or in current folder if accessible
                if folder_id:
                    # If viewing a specific folder, check if user has access
                    if str(folder_id) not in accessible_ids:
                        return {"folders": [], "documents": []}
                    # User has access to this folder, show all its contents
                else:
                    # At root/branch level, only show documents in accessible folders
                    documents = [d for d in documents if d.get("parent_id") in accessible_ids or d["id"] in accessible_ids]

        return {"folders": folders, "documents": documents}

