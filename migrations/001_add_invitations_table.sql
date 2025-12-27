-- Migration: Add organization_invitations table
-- Description: Create table for managing user invitations to organizations
-- Date: 2025-12-27

-- Create organization_invitations table
CREATE TABLE IF NOT EXISTS public.organization_invitations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE NOT NULL,
    email VARCHAR NOT NULL,
    role_id UUID REFERENCES public.roles(id) ON DELETE SET NULL,
    invited_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    invite_token VARCHAR UNIQUE NOT NULL,
    status VARCHAR DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'expired', 'cancelled')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_invitations_org ON public.organization_invitations(org_id);
CREATE INDEX IF NOT EXISTS idx_invitations_email ON public.organization_invitations(email);
CREATE INDEX IF NOT EXISTS idx_invitations_token ON public.organization_invitations(invite_token);
CREATE INDEX IF NOT EXISTS idx_invitations_status ON public.organization_invitations(status);
CREATE INDEX IF NOT EXISTS idx_invitations_expires ON public.organization_invitations(expires_at);

-- Enable Row Level Security
ALTER TABLE public.organization_invitations ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Users can view invitations for their organization
CREATE POLICY "Users can view org invitations" ON public.organization_invitations
    FOR SELECT
    USING (
        org_id IN (
            SELECT org_id FROM public.organization_members
            WHERE user_id = auth.uid()
        )
    );

-- RLS Policy: Admin users can manage invitations
CREATE POLICY "Admins can manage invitations" ON public.organization_invitations
    FOR ALL
    USING (
        org_id IN (
            SELECT om.org_id
            FROM public.organization_members om
            JOIN public.roles r ON om.role_id = r.id
            WHERE om.user_id = auth.uid()
            AND r.name IN ('Owner', 'Admin')
        )
    );

-- Add helpful comment
COMMENT ON TABLE public.organization_invitations IS 'Stores pending user invitations to organizations with expiration tracking';
COMMENT ON COLUMN public.organization_invitations.invite_token IS 'Unique token for invitation link, sent via email';
COMMENT ON COLUMN public.organization_invitations.status IS 'Invitation status: pending, accepted, expired, or cancelled';
COMMENT ON COLUMN public.organization_invitations.expires_at IS 'Invitation expiration timestamp (default 7 days from creation)';

-- Function to auto-expire invitations (optional cron job)
CREATE OR REPLACE FUNCTION expire_old_invitations()
RETURNS void AS $$
BEGIN
    UPDATE public.organization_invitations
    SET status = 'expired'
    WHERE status = 'pending'
    AND expires_at < NOW();
END;
$$ LANGUAGE plpgsql;

-- Add trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_invitations_updated_at
    BEFORE UPDATE ON public.organization_invitations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Verification query (optional - run to test)
-- SELECT COUNT(*) FROM public.organization_invitations;
