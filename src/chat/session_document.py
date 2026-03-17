"""Composed session document service."""

from .session_upload import SessionDocumentUploadMixin
from .session_processing import SessionDocumentProcessingMixin
from .session_documents_ops import SessionDocumentOpsMixin
from .session_reprocess import SessionDocumentReprocessMixin
from .session_cleanup import SessionDocumentCleanupMixin


class SessionDocumentService(
    SessionDocumentUploadMixin,
    SessionDocumentProcessingMixin,
    SessionDocumentOpsMixin,
    SessionDocumentReprocessMixin,
    SessionDocumentCleanupMixin,
):
    """Service for managing documents uploaded within chat sessions."""


from . import session_upload as _session_upload
from . import session_processing as _session_processing
from . import session_documents_ops as _session_documents_ops
from . import session_reprocess as _session_reprocess
from . import session_cleanup as _session_cleanup

_session_upload.SessionDocumentService = SessionDocumentService
_session_processing.SessionDocumentService = SessionDocumentService
_session_documents_ops.SessionDocumentService = SessionDocumentService
_session_reprocess.SessionDocumentService = SessionDocumentService
_session_cleanup.SessionDocumentService = SessionDocumentService

session_document_service = SessionDocumentService()
