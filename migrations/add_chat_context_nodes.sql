-- Add persisted document/folder context selection to chat sessions.
-- Stores an array of selected nodes as [{ "id", "name", "type" }],
-- where type is "file" or "folder". Folders are expanded to their files
-- at query time during retrieval.
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS context_nodes JSONB DEFAULT '[]'::jsonb;
