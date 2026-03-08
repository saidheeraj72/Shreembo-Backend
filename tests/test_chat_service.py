import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4
import sys
import os
import asyncio

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock settings to avoid env var errors
with patch.dict(os.environ, {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_KEY": "test-key",
    "OPENAI_API_KEY": "test-key",
    "PINECONE_API_KEY": "test-key",
    "PINECONE_ENV": "test-env",
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "AWS_REGION": "us-east-1",
    "AWS_BUCKET_NAME": "test-bucket"
}):
    from src.chat.service import ChatService
    from src.chat.session_document import SessionDocumentService

def test_delete_session_cleanup():
    asyncio.run(_test_delete_session_cleanup_async())

async def _test_delete_session_cleanup_async():
    session_id = uuid4()
    user_id = uuid4()
    
    # Mock dependencies
    # Patch pinecone_client where it is defined, since it is imported locally in the function
    with patch("src.chat.service.db") as mock_chat_db, \
         patch("src.chat.session_document.db") as mock_doc_db, \
         patch("src.chat.session_document.s3_client") as mock_s3, \
         patch("src.core.pinecone_client.pinecone_client") as mock_pinecone:
        
        # Configure Mocks
        mock_s3.delete_file = AsyncMock(return_value=True)
        mock_pinecone.delete_by_document = AsyncMock(return_value=True)
        
        # Setup mock data for session docs
        session_doc_id = uuid4()
        session_docs = [{
            "id": str(session_doc_id),
            "s3_key": "test/key",
            "session_id": str(session_id)
        }]
        
        # Mock DB chain for get_session_documents
        mock_select = MagicMock()
        mock_eq = MagicMock()
        mock_order = MagicMock()
        
        mock_doc_db.admin.table.return_value.select.return_value = mock_select
        mock_select.eq.return_value = mock_eq
        mock_eq.order.return_value = mock_order
        mock_order.execute.return_value.data = session_docs
        
        # Mock DB chain for delete session documents
        mock_delete = MagicMock()
        mock_del_eq = MagicMock()
        mock_del_execute = MagicMock()
        
        mock_doc_db.admin.table.return_value.delete.return_value = mock_delete
        mock_delete.eq.return_value = mock_del_eq
        mock_del_eq.execute.return_value.data = session_docs
        
        # Mock DB chain for delete session (soft delete)
        mock_update = MagicMock()
        mock_up_eq1 = MagicMock()
        mock_up_eq2 = MagicMock()
        mock_up_execute = MagicMock()
        
        mock_chat_db.admin.table.return_value.update.return_value = mock_update
        mock_update.eq.return_value = mock_up_eq1
        mock_up_eq1.eq.return_value = mock_up_eq2
        mock_up_execute.return_value.data = [{"id": str(session_id)}]
        
        # Execute
        result = await ChatService.delete_session(session_id, user_id)
        
        # Verify
        assert result is True
        
        # Verify S3 deletion
        mock_s3.delete_file.assert_called_with("test/key")
        
        # Verify Pinecone deletion
        assert mock_pinecone.delete_by_document.called
        call_args = mock_pinecone.delete_by_document.call_args
        assert call_args.kwargs['document_id'] == str(session_doc_id)
        assert call_args.kwargs['namespace'] == str(user_id)
