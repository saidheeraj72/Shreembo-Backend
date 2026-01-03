-- ==========================================
-- RAG CHATBOT SCHEMA
-- Chat Sessions, Messages, Session Documents, Token Usage
-- Version: 1.0
-- ==========================================

-- ==========================================
-- CHAT SESSIONS
-- ==========================================

CREATE TABLE public.chat_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,

    -- Session Info
    title TEXT DEFAULT 'New Chat',

    -- Settings (toggles)
    rag_enabled BOOLEAN DEFAULT TRUE,
    web_search_enabled BOOLEAN DEFAULT FALSE,

    -- Sharing
    is_shared BOOLEAN DEFAULT FALSE,
    shared_at TIMESTAMPTZ,
    shared_by UUID REFERENCES public.profiles(id),

    -- Status
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'archived', 'deleted')),

    -- Metadata
    message_count INT DEFAULT 0,
    last_message_at TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_chat_sessions_user ON public.chat_sessions(user_id);
CREATE INDEX idx_chat_sessions_org ON public.chat_sessions(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_chat_sessions_org_shared ON public.chat_sessions(org_id, is_shared) WHERE is_shared = TRUE;
CREATE INDEX idx_chat_sessions_status ON public.chat_sessions(user_id, status);

-- Trigger for updated_at
CREATE TRIGGER update_chat_sessions_updated_at
    BEFORE UPDATE ON public.chat_sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE public.chat_sessions IS 'RAG chatbot sessions with settings for RAG and web search toggles';

-- ==========================================
-- CHAT MESSAGES
-- ==========================================

CREATE TABLE public.chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES public.chat_sessions(id) ON DELETE CASCADE,

    -- Message Content
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,

    -- RAG Context (stored for reference/debugging)
    rag_context JSONB,  -- [{document_id, document_name, chunk_text, score}]
    web_search_results JSONB,  -- [{title, url, snippet}]

    -- Source Attribution (shown to user)
    sources JSONB,  -- [{document_id, document_name, chunk_index}]

    -- Token Usage (per message)
    prompt_tokens INT DEFAULT 0,
    completion_tokens INT DEFAULT 0,
    total_tokens INT DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_chat_messages_session ON public.chat_messages(session_id);
CREATE INDEX idx_chat_messages_created ON public.chat_messages(session_id, created_at);

COMMENT ON TABLE public.chat_messages IS 'Chat messages with RAG context and token usage tracking';

-- ==========================================
-- SESSION DOCUMENTS
-- ==========================================

CREATE TABLE public.session_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES public.chat_sessions(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES public.storage_nodes(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id),

    -- Document Info (denormalized for quick access)
    filename TEXT NOT NULL,
    file_type TEXT,
    file_size BIGINT,
    s3_key TEXT NOT NULL,

    -- Embedding Status
    embedding_status TEXT DEFAULT 'pending' CHECK (embedding_status IN ('pending', 'processing', 'completed', 'failed')),

    -- Timestamps
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

-- Indexes
CREATE INDEX idx_session_docs_session ON public.session_documents(session_id);
CREATE INDEX idx_session_docs_document ON public.session_documents(document_id);
CREATE INDEX idx_session_docs_embedding ON public.session_documents(session_id, embedding_status);

COMMENT ON TABLE public.session_documents IS 'Documents uploaded within chat sessions for RAG context';

-- ==========================================
-- TOKEN USAGE TRACKING
-- ==========================================

CREATE TABLE public.token_usage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    org_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE,  -- NULL for personal usage

    -- Usage Period
    period_start DATE NOT NULL,  -- Monthly aggregation (first day of month)

    -- Token Counts
    prompt_tokens BIGINT DEFAULT 0,
    completion_tokens BIGINT DEFAULT 0,
    total_tokens BIGINT DEFAULT 0,

    -- Request Counts
    chat_requests INT DEFAULT 0,
    rag_requests INT DEFAULT 0,
    web_search_requests INT DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Unique constraint: one record per user per org (or personal) per month
    UNIQUE(user_id, org_id, period_start)
);

-- Indexes
CREATE INDEX idx_token_usage_user ON public.token_usage(user_id);
CREATE INDEX idx_token_usage_org ON public.token_usage(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_token_usage_period ON public.token_usage(period_start);
CREATE INDEX idx_token_usage_user_period ON public.token_usage(user_id, period_start);

-- Trigger for updated_at
CREATE TRIGGER update_token_usage_updated_at
    BEFORE UPDATE ON public.token_usage
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE public.token_usage IS 'Monthly token usage tracking per user for org and personal accounts';

-- ==========================================
-- HELPER FUNCTION: INCREMENT TOKEN USAGE
-- ==========================================

CREATE OR REPLACE FUNCTION increment_token_usage(
    p_user_id UUID,
    p_org_id UUID,
    p_prompt_tokens INT,
    p_completion_tokens INT,
    p_is_rag BOOLEAN DEFAULT FALSE,
    p_is_web_search BOOLEAN DEFAULT FALSE
)
RETURNS void AS $$
DECLARE
    v_period_start DATE := date_trunc('month', CURRENT_DATE)::DATE;
BEGIN
    INSERT INTO public.token_usage (
        user_id, org_id, period_start,
        prompt_tokens, completion_tokens, total_tokens,
        chat_requests, rag_requests, web_search_requests
    )
    VALUES (
        p_user_id, p_org_id, v_period_start,
        p_prompt_tokens, p_completion_tokens, p_prompt_tokens + p_completion_tokens,
        1,
        CASE WHEN p_is_rag THEN 1 ELSE 0 END,
        CASE WHEN p_is_web_search THEN 1 ELSE 0 END
    )
    ON CONFLICT (user_id, org_id, period_start)
    DO UPDATE SET
        prompt_tokens = token_usage.prompt_tokens + EXCLUDED.prompt_tokens,
        completion_tokens = token_usage.completion_tokens + EXCLUDED.completion_tokens,
        total_tokens = token_usage.total_tokens + EXCLUDED.total_tokens,
        chat_requests = token_usage.chat_requests + 1,
        rag_requests = token_usage.rag_requests + EXCLUDED.rag_requests,
        web_search_requests = token_usage.web_search_requests + EXCLUDED.web_search_requests,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION increment_token_usage IS 'Atomically increment token usage with upsert for monthly aggregation';

-- ==========================================
-- ROW LEVEL SECURITY
-- ==========================================

-- Enable RLS
ALTER TABLE public.chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.session_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.token_usage ENABLE ROW LEVEL SECURITY;

-- Chat Sessions: Users can view their own sessions or shared sessions in their org
CREATE POLICY "Users can view own or shared sessions" ON public.chat_sessions
    FOR SELECT USING (
        user_id = auth.uid() OR
        (is_shared = true AND org_id IN (
            SELECT org_id FROM public.organization_members
            WHERE user_id = auth.uid() AND status = 'active'
        ))
    );

CREATE POLICY "Users can insert own sessions" ON public.chat_sessions
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update own sessions" ON public.chat_sessions
    FOR UPDATE USING (user_id = auth.uid());

CREATE POLICY "Users can delete own sessions" ON public.chat_sessions
    FOR DELETE USING (user_id = auth.uid());

-- Chat Messages: Users can access messages in accessible sessions
CREATE POLICY "Users can view messages in accessible sessions" ON public.chat_messages
    FOR SELECT USING (
        session_id IN (
            SELECT id FROM public.chat_sessions
            WHERE user_id = auth.uid() OR
            (is_shared = true AND org_id IN (
                SELECT org_id FROM public.organization_members
                WHERE user_id = auth.uid() AND status = 'active'
            ))
        )
    );

CREATE POLICY "Users can insert messages in own sessions" ON public.chat_messages
    FOR INSERT WITH CHECK (
        session_id IN (
            SELECT id FROM public.chat_sessions WHERE user_id = auth.uid()
        )
    );

-- Session Documents: Users can access docs in accessible sessions
CREATE POLICY "Users can view docs in accessible sessions" ON public.session_documents
    FOR SELECT USING (
        session_id IN (
            SELECT id FROM public.chat_sessions
            WHERE user_id = auth.uid() OR
            (is_shared = true AND org_id IN (
                SELECT org_id FROM public.organization_members
                WHERE user_id = auth.uid() AND status = 'active'
            ))
        )
    );

CREATE POLICY "Users can insert docs in own sessions" ON public.session_documents
    FOR INSERT WITH CHECK (user_id = auth.uid());

-- Token Usage: Users can view their own usage
CREATE POLICY "Users can view own token usage" ON public.token_usage
    FOR SELECT USING (user_id = auth.uid());

-- ==========================================
-- END OF CHAT SCHEMA
-- ==========================================
