-- Add user access control fields to organization_members table
ALTER TABLE public.organization_members
ADD COLUMN IF NOT EXISTS rag_enabled BOOLEAN DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS chat_enabled BOOLEAN DEFAULT TRUE;

-- Add comments
COMMENT ON COLUMN public.organization_members.rag_enabled IS 'Whether user can use RAG (document search) features';
COMMENT ON COLUMN public.organization_members.chat_enabled IS 'Whether user can use chat features';
