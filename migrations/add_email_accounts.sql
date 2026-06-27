-- ==========================================
-- EMAIL AGENT — CONNECTED MAILBOXES
-- Stores per-user OAuth-connected email accounts (Gmail, Outlook).
-- OAuth tokens are stored ENCRYPTED (Fernet) by the application layer.
-- Version: 1.0
-- ==========================================

CREATE TABLE IF NOT EXISTS public.email_accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,

    -- Provider info
    provider TEXT NOT NULL CHECK (provider IN ('google', 'microsoft')),
    email_address TEXT NOT NULL,

    -- OAuth tokens (encrypted at the application layer before insert)
    refresh_token TEXT NOT NULL,
    access_token TEXT,
    token_expires_at TIMESTAMPTZ,
    scopes TEXT,

    -- Status
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked', 'error')),

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (user_id, provider, email_address)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_email_accounts_user ON public.email_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_email_accounts_user_status ON public.email_accounts(user_id, status);

-- Trigger for updated_at (reuses existing helper from chat_schema.sql)
DROP TRIGGER IF EXISTS update_email_accounts_updated_at ON public.email_accounts;
CREATE TRIGGER update_email_accounts_updated_at
    BEFORE UPDATE ON public.email_accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE public.email_accounts IS 'OAuth-connected mailboxes for the email agent. Tokens stored encrypted.';
