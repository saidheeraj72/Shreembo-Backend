"""Document service for CRUD operations."""
import zipfile
import mimetypes
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime
from io import BytesIO

from src.core.database import db
from src.core.s3 import s3_client
from src.core.websocket import ws_manager
from src.services.embedding_service import embedding_service


class DocumentService:
    @staticmethod
    async def get_branches(org_id: UUID) -> List[dict]:
        """Get active branches for organization."""
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
    async def create_folder(org_id: UUID, owner_id: UUID, name: str,
                           parent_id: Optional[UUID] = None, 
                           branch_id: Optional[UUID] = None,
                           description: str = None) -> dict:
        data = {
            "org_id": str(org_id),
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
    async def get_folder(folder_id: UUID, org_id: UUID) -> Optional[dict]:
        result = db.admin.table("storage_nodes").select("*").eq(
            "id", str(folder_id)
        ).eq("org_id", str(org_id)).eq("node_type", "folder").maybe_single().execute()
        return result.data

    @staticmethod
    async def get_folder_contents(org_id: UUID, 
                                folder_id: Optional[UUID] = None,
                                branch_id: Optional[UUID] = None) -> dict:
        query = db.admin.table("storage_nodes").select("*").eq(
            "org_id", str(org_id)
        ).eq("status", "active")

        if folder_id:
            query = query.eq("parent_id", str(folder_id))
        elif branch_id:
            # Root of a branch
            query = query.eq("branch_id", str(branch_id)).is_("parent_id", "null")
        else:
            # Fallback if neither provided (should technically be unreachable via API logic)
            query = query.is_("parent_id", "null").is_("branch_id", "null")

        result = query.order("node_type").order("name").execute()

        folders = [r for r in result.data if r["node_type"] == "folder"]
        documents = [r for r in result.data if r["node_type"] == "file"]

        return {"folders": folders, "documents": documents}

    @staticmethod
    async def init_upload(org_id: UUID, owner_id: UUID, filename: str, content_type: str,
                          size_bytes: int, parent_id: Optional[UUID] = None,
                          branch_id: Optional[UUID] = None) -> dict:
        upload_id = str(uuid4())
        s3_key = s3_client.generate_key(str(org_id), filename)

        presigned = await s3_client.get_presigned_upload_url(s3_key, content_type)

        # Store pending upload info in cache or temp storage
        return {
            "upload_id": upload_id,
            "upload_url": presigned["upload_url"],
            "s3_key": s3_key,
            "org_id": str(org_id),
            "owner_id": str(owner_id),
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "parent_id": str(parent_id) if parent_id else None,
            "branch_id": str(branch_id) if branch_id else None,
        }

    @staticmethod
    async def complete_upload(org_id: UUID, owner_id: UUID, upload_id: str, s3_key: str,
                              filename: str, content_type: str, size_bytes: int,
                              parent_id: Optional[UUID] = None, 
                              branch_id: Optional[UUID] = None,
                              description: str = None,
                              tags: List[str] = None) -> dict:
        # Create document record
        ext = filename.rsplit(".", 1)[-1] if "." in filename else None
        data = {
            "org_id": str(org_id),
            "owner_id": str(owner_id),
            "name": filename,
            "node_type": "file",
            "mime_type": content_type,
            "file_size": size_bytes,
            "file_extension": ext,
            "s3_key": s3_key,
            "s3_bucket": None,  # Will be set from settings
            "parent_id": str(parent_id) if parent_id else None,
            "branch_id": str(branch_id) if branch_id else None,
            "description": description,
            "tags": tags,
            "processing_status": "pending",
            "embedding_status": "pending",
        }

        result = db.admin.table("storage_nodes").insert(data).execute()
        document = result.data[0] if result.data else None

        if document:
            # Send WebSocket progress
            await ws_manager.send_upload_progress(
                str(owner_id), upload_id, "processing", 30, document["id"]
            )

            # Queue embedding generation (async)
            await embedding_service.process_document(
                document_id=UUID(document["id"]),
                org_id=org_id,
                s3_key=s3_key,
                file_type=ext,
                user_id=str(owner_id),
                upload_id=upload_id
            )

        return document

    @staticmethod
    async def get_document(doc_id: UUID, org_id: UUID) -> Optional[dict]:
        result = db.admin.table("storage_nodes").select("*").eq(
            "id", str(doc_id)
        ).eq("org_id", str(org_id)).maybe_single().execute()
        return result.data

    @staticmethod
    async def update_document(doc_id: UUID, org_id: UUID, **updates) -> Optional[dict]:
        updates["updated_at"] = datetime.utcnow().isoformat()
        result = db.admin.table("storage_nodes").update(updates).eq(
            "id", str(doc_id)
        ).eq("org_id", str(org_id)).execute()
        return result.data[0] if result.data else None

    @staticmethod
    async def delete_document(doc_id: UUID, org_id: UUID) -> bool:
        doc = await DocumentService.get_document(doc_id, org_id)
        if not doc:
            return False

        # Delete from S3
        if doc.get("s3_key"):
            await s3_client.delete_file(doc["s3_key"])

        # Delete from Pinecone
        from src.core.pinecone_client import pinecone_client
        await pinecone_client.delete_by_document(str(doc_id), str(org_id))

        # Soft delete in DB
        db.admin.table("storage_nodes").update({
            "status": "deleted",
            "deleted_at": datetime.utcnow().isoformat()
        }).eq("id", str(doc_id)).execute()

        return True

    @staticmethod
    async def move_document(doc_id: UUID, org_id: UUID, target_folder_id: Optional[UUID]) -> Optional[dict]:
        return await DocumentService.update_document(
            doc_id, org_id,
            parent_id=str(target_folder_id) if target_folder_id else None
        )

    @staticmethod
    async def get_download_url(doc_id: UUID, org_id: UUID) -> Optional[str]:
        doc = await DocumentService.get_document(doc_id, org_id)
        if not doc or not doc.get("s3_key"):
            return None
        return await s3_client.get_presigned_download_url(doc["s3_key"], doc["name"])

    @staticmethod
    async def get_view_url(doc_id: UUID, org_id: UUID) -> Optional[str]:
        """Get presigned URL for viewing document in browser."""
        doc = await DocumentService.get_document(doc_id, org_id)
        if not doc or not doc.get("s3_key"):
            return None
        return await s3_client.get_presigned_view_url(doc["s3_key"], doc.get("mime_type"))

    @staticmethod
    async def replicate_document(doc_id: UUID, org_id: UUID, target_branch_id: UUID) -> Optional[dict]:
        """Replicate a document to another branch, including file, metadata, and embeddings."""
        from src.core.pinecone_client import pinecone_client

        # Get source document
        source_doc = await DocumentService.get_document(doc_id, org_id)
        if not source_doc or source_doc.get("node_type") != "file":
            return None

        # Generate new S3 key for the copy
        new_s3_key = s3_client.generate_key(str(org_id), source_doc["name"])

        # Copy file in S3
        if source_doc.get("s3_key"):
            await s3_client.copy_file(source_doc["s3_key"], new_s3_key)

        # Create new document record
        new_doc_data = {
            "org_id": str(org_id),
            "owner_id": source_doc["owner_id"],
            "name": source_doc["name"],
            "node_type": "file",
            "mime_type": source_doc.get("mime_type"),
            "file_size": source_doc.get("file_size"),
            "file_extension": source_doc.get("file_extension"),
            "s3_key": new_s3_key,
            "s3_bucket": source_doc.get("s3_bucket"),
            "parent_id": None,  # Root of target branch
            "branch_id": str(target_branch_id),
            "description": source_doc.get("description"),
            "tags": source_doc.get("tags"),
            "processing_status": source_doc.get("processing_status"),
            "embedding_status": source_doc.get("embedding_status"),
        }

        result = db.admin.table("storage_nodes").insert(new_doc_data).execute()
        new_doc = result.data[0] if result.data else None

        if new_doc and source_doc.get("embedding_status") == "completed":
            # Copy embeddings from source to target
            await pinecone_client.copy_embeddings(
                source_doc_id=str(doc_id),
                target_doc_id=new_doc["id"],
                source_namespace=str(org_id),
                target_namespace=str(org_id)
            )

        return new_doc

    @staticmethod
    async def process_zip_upload(
        zip_bytes: bytes,
        org_id: UUID,
        owner_id: UUID,
        branch_id: UUID,
        parent_id: Optional[UUID] = None,
        user_id: str = None,
        upload_id: str = None
    ) -> Dict[str, Any]:
        """Process a ZIP file upload, extracting and creating folder structure."""
        created_folders: Dict[str, str] = {}  # path -> folder_id mapping
        created_files: List[dict] = []
        errors: List[str] = []

        try:
            zip_buffer = BytesIO(zip_bytes)
            with zipfile.ZipFile(zip_buffer, 'r') as zf:
                # Get all entries sorted by path depth (folders first)
                entries = sorted(zf.namelist(), key=lambda x: (x.count('/'), x))

                total_entries = len([e for e in entries if not e.endswith('/')])
                processed = 0

                for entry in entries:
                    # Skip hidden files and __MACOSX
                    if entry.startswith('__MACOSX') or '/.' in entry or entry.startswith('.'):
                        continue

                    if entry.endswith('/'):
                        # It's a folder
                        folder_path = entry.rstrip('/')
                        if folder_path and folder_path not in created_folders:
                            # Determine parent
                            path_parts = folder_path.rsplit('/', 1)
                            folder_name = path_parts[-1] if len(path_parts) > 1 else folder_path
                            parent_path = path_parts[0] if len(path_parts) > 1 else None

                            folder_parent_id = None
                            if parent_path and parent_path in created_folders:
                                folder_parent_id = created_folders[parent_path]
                            elif parent_id:
                                folder_parent_id = str(parent_id)

                            # Create folder
                            folder = await DocumentService.create_folder(
                                org_id=org_id,
                                owner_id=owner_id,
                                name=folder_name,
                                parent_id=UUID(folder_parent_id) if folder_parent_id else None,
                                branch_id=branch_id
                            )
                            if folder:
                                created_folders[folder_path] = folder["id"]
                    else:
                        # It's a file
                        try:
                            file_data = zf.read(entry)
                            if len(file_data) == 0:
                                continue

                            # Get file info
                            path_parts = entry.rsplit('/', 1)
                            filename = path_parts[-1] if len(path_parts) > 1 else entry
                            parent_path = path_parts[0] if len(path_parts) > 1 else None

                            # Skip if filename is empty
                            if not filename:
                                continue

                            # Determine parent folder
                            file_parent_id = None
                            if parent_path and parent_path in created_folders:
                                file_parent_id = created_folders[parent_path]
                            elif parent_id:
                                file_parent_id = str(parent_id)

                            # Determine content type
                            content_type, _ = mimetypes.guess_type(filename)
                            content_type = content_type or 'application/octet-stream'

                            # Generate S3 key and upload
                            s3_key = s3_client.generate_key(str(org_id), filename)
                            await s3_client.upload_file_bytes(file_data, s3_key, content_type)

                            # Create document record
                            ext = filename.rsplit(".", 1)[-1] if "." in filename else None
                            doc_data = {
                                "org_id": str(org_id),
                                "owner_id": str(owner_id),
                                "name": filename,
                                "node_type": "file",
                                "mime_type": content_type,
                                "file_size": len(file_data),
                                "file_extension": ext,
                                "s3_key": s3_key,
                                "parent_id": file_parent_id,
                                "branch_id": str(branch_id),
                                "processing_status": "pending",
                                "embedding_status": "pending",
                            }

                            result = db.admin.table("storage_nodes").insert(doc_data).execute()
                            document = result.data[0] if result.data else None

                            if document:
                                created_files.append(document)
                                processed += 1

                                # Send progress update
                                if user_id and upload_id:
                                    progress = int((processed / total_entries) * 100) if total_entries > 0 else 100
                                    await ws_manager.send_upload_progress(
                                        user_id, upload_id, "processing", progress, document["id"]
                                    )

                                # Queue embedding generation
                                await embedding_service.process_document(
                                    document_id=UUID(document["id"]),
                                    org_id=org_id,
                                    s3_key=s3_key,
                                    file_type=ext,
                                    user_id=user_id,
                                    upload_id=upload_id
                                )

                        except Exception as e:
                            errors.append(f"Failed to process {entry}: {str(e)}")

            return {
                "success": True,
                "folders_created": len(created_folders),
                "files_created": len(created_files),
                "files": created_files,
                "errors": errors
            }

        except zipfile.BadZipFile:
            return {
                "success": False,
                "error": "Invalid ZIP file",
                "folders_created": 0,
                "files_created": 0,
                "files": [],
                "errors": ["Invalid or corrupted ZIP file"]
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "folders_created": len(created_folders),
                "files_created": len(created_files),
                "files": created_files,
                "errors": errors + [str(e)]
            }


document_service = DocumentService()