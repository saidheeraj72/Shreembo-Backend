-- ==========================================
-- EMAIL AGENT — ISSUE SCAN CACHE
-- Caches the latest "recurring issues" scan per connected mailbox so the
-- Issues table loads instantly from DB; a re-scan overwrites the snapshot.
-- Version: 1.0
-- ==========================================

CREATE TABLE IF NOT EXISTS public.email_issue_scans (
    -- One cached snapshot per account; a re-scan upserts on this key.
    account_id UUID PRIMARY KEY REFERENCES public.email_accounts(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,

    -- The scan result.
    issues JSONB NOT NULL DEFAULT '[]'::jsonb,
    email_count INT NOT NULL DEFAULT 0,
    query TEXT,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for scoping reads to a user.
CREATE INDEX IF NOT EXISTS idx_email_issue_scans_user ON public.email_issue_scans(user_id);

-- Trigger for updated_at (reuses existing helper from chat_schema.sql)
DROP TRIGGER IF EXISTS update_email_issue_scans_updated_at ON public.email_issue_scans;
CREATE TRIGGER update_email_issue_scans_updated_at
    BEFORE UPDATE ON public.email_issue_scans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE public.email_issue_scans IS 'Latest cached recurring-issues scan per connected mailbox.';
