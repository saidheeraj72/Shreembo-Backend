-- Add attachments field to chat_messages
-- This allows messages to reference uploaded documents

ALTER TABLE public.chat_messages
ADD COLUMN IF NOT EXISTS attachments JSONB;

COMMENT ON COLUMN public.chat_messages.attachments IS 'Array of document attachments: [{session_document_id, document_id, filename, file_type, file_size}]';
