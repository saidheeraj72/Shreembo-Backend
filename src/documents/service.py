"""Composed document service."""

from .folders import DocumentFoldersMixin
from .uploads import DocumentUploadsMixin
from .operations import DocumentOperationsMixin
from .replication import DocumentReplicationMixin


class DocumentService(
    DocumentFoldersMixin,
    DocumentUploadsMixin,
    DocumentOperationsMixin,
    DocumentReplicationMixin,
):
    """Service for folder/document lifecycle and storage."""


from . import operations as _operations
from . import replication as _replication

_operations.DocumentService = DocumentService
_replication.DocumentService = DocumentService

# Global document service instance
document_service = DocumentService()
