-- ==========================================
-- UPDATE SESSION DOCUMENTS SCHEMA
-- Remove storage_nodes dependency for session documents
-- Version: 1.1
-- ==========================================

-- Session documents are now independent of storage_nodes
-- They only exist in session_documents table with their own lifecycle

-- Drop foreign key constraint from document_id
ALTER TABLE public.session_documents
DROP CONSTRAINT IF EXISTS session_documents_document_id_fkey;

-- Make document_id nullable (we'll deprecate it in favor of using the session_documents.id)
ALTER TABLE public.session_documents
ALTER COLUMN document_id DROP NOT NULL;

-- Add mime_type column if it doesn't exist
ALTER TABLE public.session_documents
ADD COLUMN IF NOT EXISTS mime_type TEXT;

-- Create index on session_id and embedding_status for efficient queries
CREATE INDEX IF NOT EXISTS idx_session_docs_status
ON public.session_documents(session_id, embedding_status);

COMMENT ON TABLE public.session_documents IS 'Session-specific documents stored independently from storage_nodes. Uses session_documents.id as the document identifier in Pinecone chat-sessions index.';
COMMENT ON COLUMN public.session_documents.document_id IS 'DEPRECATED: Legacy column for storage_nodes reference. Use session_documents.id instead.';
COMMENT ON COLUMN public.session_documents.mime_type IS 'MIME type of the uploaded file (e.g., application/pdf, text/plain)';
