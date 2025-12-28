-- Document Repository Extensions
-- Run after enterprise_schema.sql

-- Add S3 columns to storage_nodes
ALTER TABLE public.storage_nodes
ADD COLUMN IF NOT EXISTS s3_bucket TEXT,
ADD COLUMN IF NOT EXISTS s3_key TEXT,
ADD COLUMN IF NOT EXISTS s3_region TEXT DEFAULT 'us-east-1',
ADD COLUMN IF NOT EXISTS processing_status TEXT DEFAULT 'pending',
ADD COLUMN IF NOT EXISTS embedding_status TEXT DEFAULT 'pending';

CREATE INDEX IF NOT EXISTS idx_nodes_s3 ON public.storage_nodes(s3_bucket, s3_key) WHERE s3_key IS NOT NULL;

-- Share links table
CREATE TABLE IF NOT EXISTS public.share_links (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    node_id UUID NOT NULL REFERENCES public.storage_nodes(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    token TEXT UNIQUE NOT NULL DEFAULT encode(gen_random_bytes(32), 'hex'),
    permission share_permission DEFAULT 'view',
    password_hash TEXT,
    expires_at TIMESTAMPTZ,
    max_access_count INT,
    access_count INT DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    name TEXT,
    created_by UUID NOT NULL REFERENCES public.profiles(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_share_links_token ON public.share_links(token);
CREATE INDEX IF NOT EXISTS idx_share_links_node ON public.share_links(node_id);
