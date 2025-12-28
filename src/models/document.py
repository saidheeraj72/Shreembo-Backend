"""Document and folder models."""
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class NodeType(str, Enum):
    FILE = "file"
    FOLDER = "folder"
    BRANCH = "branch"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# Request Models
class FolderCreate(BaseModel):
    name: str
    parent_id: Optional[UUID] = None
    branch_id: Optional[UUID] = None
    description: Optional[str] = None


class FolderUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class DocumentUploadInit(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    parent_id: Optional[UUID] = None
    branch_id: Optional[UUID] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None


class DocumentUploadComplete(BaseModel):
    upload_id: str
    s3_key: str


class DocumentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None


class DocumentMove(BaseModel):
    target_folder_id: Optional[UUID] = None


class DocumentReplicate(BaseModel):
    target_branch_id: UUID


# Response Models
class DocumentResponse(BaseModel):
    id: UUID
    name: str
    node_type: NodeType
    parent_id: Optional[UUID] = None
    branch_id: Optional[UUID] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    file_extension: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    owner_id: UUID
    processing_status: Optional[str] = None
    embedding_status: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FolderResponse(BaseModel):
    id: UUID
    name: str
    node_type: NodeType = NodeType.FOLDER
    parent_id: Optional[UUID] = None
    branch_id: Optional[UUID] = None
    description: Optional[str] = None
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    children_count: Optional[int] = 0

    class Config:
        from_attributes = True


class FolderContents(BaseModel):
    folder: Optional[FolderResponse] = None
    folders: List[FolderResponse] = []
    documents: List[DocumentResponse] = []


class UploadInitResponse(BaseModel):
    upload_id: str
    upload_url: str
    s3_key: str


class SearchResult(BaseModel):
    document: DocumentResponse
    score: float
    chunk_text: Optional[str] = None
