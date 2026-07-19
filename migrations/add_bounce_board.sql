-- Bounce Board — analysis sessions + issue knowledge base.
-- Apply manually in the Supabase SQL editor (this repo has no migration runner).
-- Requires the update_updated_at_column() helper from chat_schema.sql.

CREATE TABLE IF NOT EXISTS public.bounce_board_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    org_id UUID,
    title TEXT NOT NULL,
    problem_excerpt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'running', 'complete', 'error')),
    current_stage TEXT NOT NULL DEFAULT 'intake',
    stages JSONB NOT NULL DEFAULT '[]'::jsonb,
    input JSONB NOT NULL DEFAULT '{}'::jsonb,
    context JSONB,
    routed_agent_id TEXT,
    retrieved_sources JSONB,
    frameworks JSONB,
    discussion JSONB,
    recommendations JSONB,
    report JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bounce_board_sessions_user
    ON public.bounce_board_sessions (user_id, updated_at DESC);

DROP TRIGGER IF EXISTS update_bounce_board_sessions_updated_at ON public.bounce_board_sessions;
CREATE TRIGGER update_bounce_board_sessions_updated_at
    BEFORE UPDATE ON public.bounce_board_sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


CREATE TABLE IF NOT EXISTS public.bounce_board_kb_issues (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID,
    industry TEXT NOT NULL CHECK (industry IN ('shipping', 'healthcare', 'manufacturing', 'logistics')),
    source_type TEXT NOT NULL CHECK (source_type IN ('regulation', 'sop', 'manual', 'incident', 'best_practice', 'company_doc')),
    severity TEXT NOT NULL DEFAULT 'medium' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    content_md TEXT NOT NULL DEFAULT '',
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    related_issue_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    times_referenced INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bounce_board_kb_industry
    ON public.bounce_board_kb_issues (industry, source_type);

DROP TRIGGER IF EXISTS update_bounce_board_kb_issues_updated_at ON public.bounce_board_kb_issues;
CREATE TRIGGER update_bounce_board_kb_issues_updated_at
    BEFORE UPDATE ON public.bounce_board_kb_issues
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
