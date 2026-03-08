"""Auto-split document service part."""
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from urllib.parse import quote
import zipfile
from io import BytesIO

from src.core.database import db
from src.core.s3 import s3_client
from src.core.exceptions import NotFoundError, ValidationError, ConflictError
from src.llm.embedding import embedding_service


class DocumentReplicationMixin:
    @staticmethod
    async def replicate_document(doc_id: UUID, org_id: Optional[UUID], target_branch_id: UUID) -> Optional[dict]:
        """Replicate a document to another branch, including file, metadata, and embeddings."""
        from src.core.qdrant_client import qdrant_client

        # Get source document
        source_doc = await DocumentService.get_document(doc_id, org_id)
        if not source_doc or source_doc.get("node_type") != "file":
            return None
            
        # Ensure target branch belongs to same org (basic check, could be more robust)
        # If org_id is None (personal), we probably shouldn't allow replicating to a branch 
        # unless we support moving from personal to org. For now, assume org_id must exist for branches.
        if not org_id:
            return None # Personal docs can't be replicated to branches (which belong to orgs) yet

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
            await qdrant_client.copy_embeddings(
                source_doc_id=str(doc_id),
                target_doc_id=new_doc["id"],
                source_namespace=str(org_id),
                target_namespace=str(org_id)
            )

        return new_doc

    @staticmethod
    async def process_zip_upload(
        zip_bytes: bytes,
        org_id: Optional[UUID],
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
                            # Use org_id or owner_id as prefix
                            prefix = str(org_id) if org_id else str(owner_id)
                            s3_key = s3_client.generate_key(prefix, filename)
                            await s3_client.upload_file_bytes(file_data, s3_key, content_type)

                            # Create document record
                            ext = filename.rsplit(".", 1)[-1] if "." in filename else None
                            doc_data = {
                                "org_id": str(org_id) if org_id else None,
                                "owner_id": str(owner_id),
                                "name": filename,
                                "node_type": "file",
                                "mime_type": content_type,
                                "file_size": len(file_data),
                                "file_extension": ext,
                                "s3_key": s3_key,
                                "parent_id": file_parent_id,
                                "branch_id": str(branch_id) if branch_id else None,
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

                                # Start background embedding generation
                                asyncio.create_task(
                                    embedding_service.process_document(
                                        document_id=UUID(document["id"]),
                                        org_id=org_id,
                                        s3_key=s3_key,
                                        file_type=ext,
                                        user_id=user_id,
                                        upload_id=upload_id
                                    )
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

