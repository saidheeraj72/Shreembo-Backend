-- Migration: Add usage limits tables
-- Description: Add tables for managing user and organization usage limits

-- Usage Limits Table
CREATE TABLE IF NOT EXISTS usage_limits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('user', 'organization')),
    entity_id UUID NOT NULL,
    limit_type TEXT NOT NULL CHECK (limit_type IN ('monthly_tokens', 'daily_rag_requests', 'daily_chat_requests', 'requests_per_minute')),
    limit_value BIGINT NOT NULL DEFAULT 0,
    period TEXT NOT NULL CHECK (period IN ('daily', 'monthly', 'minute')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(entity_type, entity_id, limit_type)
);

-- Create indexes for efficient lookups
CREATE INDEX IF NOT EXISTS idx_usage_limits_entity ON usage_limits(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_usage_limits_type ON usage_limits(limit_type);

-- Usage Tracking (for rate limiting per minute)
CREATE TABLE IF NOT EXISTS usage_rate_tracking (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    org_id UUID,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, org_id, window_start)
);

-- Create indexes for rate tracking
CREATE INDEX IF NOT EXISTS idx_usage_rate_user ON usage_rate_tracking(user_id, window_start);
CREATE INDEX IF NOT EXISTS idx_usage_rate_org ON usage_rate_tracking(org_id, window_start);

-- Daily Usage Aggregation (for daily limits)
CREATE TABLE IF NOT EXISTS usage_daily_summary (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    org_id UUID,
    usage_date DATE NOT NULL,
    chat_requests INT NOT NULL DEFAULT 0,
    rag_requests INT NOT NULL DEFAULT 0,
    web_search_requests INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, org_id, usage_date)
);

-- Create indexes for daily summary
CREATE INDEX IF NOT EXISTS idx_usage_daily_user_date ON usage_daily_summary(user_id, usage_date);
CREATE INDEX IF NOT EXISTS idx_usage_daily_org_date ON usage_daily_summary(org_id, usage_date);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers for updated_at
DROP TRIGGER IF EXISTS update_usage_limits_updated_at ON usage_limits;
CREATE TRIGGER update_usage_limits_updated_at
    BEFORE UPDATE ON usage_limits
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_usage_rate_tracking_updated_at ON usage_rate_tracking;
CREATE TRIGGER update_usage_rate_tracking_updated_at
    BEFORE UPDATE ON usage_rate_tracking
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_usage_daily_summary_updated_at ON usage_daily_summary;
CREATE TRIGGER update_usage_daily_summary_updated_at
    BEFORE UPDATE ON usage_daily_summary
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Function to increment daily usage
CREATE OR REPLACE FUNCTION increment_daily_usage(
    p_user_id UUID,
    p_org_id UUID,
    p_chat BOOLEAN DEFAULT FALSE,
    p_rag BOOLEAN DEFAULT FALSE,
    p_web BOOLEAN DEFAULT FALSE
)
RETURNS VOID AS $$
BEGIN
    INSERT INTO usage_daily_summary (user_id, org_id, usage_date, chat_requests, rag_requests, web_search_requests)
    VALUES (
        p_user_id,
        p_org_id,
        CURRENT_DATE,
        CASE WHEN p_chat THEN 1 ELSE 0 END,
        CASE WHEN p_rag THEN 1 ELSE 0 END,
        CASE WHEN p_web THEN 1 ELSE 0 END
    )
    ON CONFLICT (user_id, org_id, usage_date)
    DO UPDATE SET
        chat_requests = usage_daily_summary.chat_requests + CASE WHEN p_chat THEN 1 ELSE 0 END,
        rag_requests = usage_daily_summary.rag_requests + CASE WHEN p_rag THEN 1 ELSE 0 END,
        web_search_requests = usage_daily_summary.web_search_requests + CASE WHEN p_web THEN 1 ELSE 0 END,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;

-- Function to increment rate tracking
CREATE OR REPLACE FUNCTION increment_rate_tracking(
    p_user_id UUID,
    p_org_id UUID,
    p_window_start TIMESTAMPTZ
)
RETURNS INT AS $$
DECLARE
    current_count INT;
BEGIN
    INSERT INTO usage_rate_tracking (user_id, org_id, window_start, request_count)
    VALUES (p_user_id, p_org_id, p_window_start, 1)
    ON CONFLICT (user_id, org_id, window_start)
    DO UPDATE SET
        request_count = usage_rate_tracking.request_count + 1,
        updated_at = NOW()
    RETURNING request_count INTO current_count;

    RETURN current_count;
END;
$$ LANGUAGE plpgsql;

-- Insert default limits for existing users and orgs
-- These are placeholder values - adjust based on your needs

-- Default user limits
INSERT INTO usage_limits (entity_type, entity_id, limit_type, limit_value, period)
SELECT
    'user' as entity_type,
    id as entity_id,
    'monthly_tokens' as limit_type,
    100000 as limit_value,
    'monthly' as period
FROM profiles
ON CONFLICT (entity_type, entity_id, limit_type) DO NOTHING;

INSERT INTO usage_limits (entity_type, entity_id, limit_type, limit_value, period)
SELECT
    'user' as entity_type,
    id as entity_id,
    'daily_rag_requests' as limit_type,
    50 as limit_value,
    'daily' as period
FROM profiles
ON CONFLICT (entity_type, entity_id, limit_type) DO NOTHING;

INSERT INTO usage_limits (entity_type, entity_id, limit_type, limit_value, period)
SELECT
    'user' as entity_type,
    id as entity_id,
    'daily_chat_requests' as limit_type,
    100 as limit_value,
    'daily' as period
FROM profiles
ON CONFLICT (entity_type, entity_id, limit_type) DO NOTHING;

INSERT INTO usage_limits (entity_type, entity_id, limit_type, limit_value, period)
SELECT
    'user' as entity_type,
    id as entity_id,
    'requests_per_minute' as limit_type,
    10 as limit_value,
    'minute' as period
FROM profiles
ON CONFLICT (entity_type, entity_id, limit_type) DO NOTHING;

-- Default organization limits
INSERT INTO usage_limits (entity_type, entity_id, limit_type, limit_value, period)
SELECT
    'organization' as entity_type,
    id as entity_id,
    'monthly_tokens' as limit_type,
    1000000 as limit_value,
    'monthly' as period
FROM organizations
ON CONFLICT (entity_type, entity_id, limit_type) DO NOTHING;

INSERT INTO usage_limits (entity_type, entity_id, limit_type, limit_value, period)
SELECT
    'organization' as entity_type,
    id as entity_id,
    'daily_rag_requests' as limit_type,
    500 as limit_value,
    'daily' as period
FROM organizations
ON CONFLICT (entity_type, entity_id, limit_type) DO NOTHING;

INSERT INTO usage_limits (entity_type, entity_id, limit_type, limit_value, period)
SELECT
    'organization' as entity_type,
    id as entity_id,
    'daily_chat_requests' as limit_type,
    1000 as limit_value,
    'daily' as period
FROM organizations
ON CONFLICT (entity_type, entity_id, limit_type) DO NOTHING;

INSERT INTO usage_limits (entity_type, entity_id, limit_type, limit_value, period)
SELECT
    'organization' as entity_type,
    id as entity_id,
    'requests_per_minute' as limit_type,
    50 as limit_value,
    'minute' as period
FROM organizations
ON CONFLICT (entity_type, entity_id, limit_type) DO NOTHING;

-- Cleanup old rate tracking data (keep only last hour)
CREATE OR REPLACE FUNCTION cleanup_old_rate_tracking()
RETURNS VOID AS $$
BEGIN
    DELETE FROM usage_rate_tracking
    WHERE window_start < NOW() - INTERVAL '1 hour';
END;
$$ LANGUAGE plpgsql;

-- Comments
COMMENT ON TABLE usage_limits IS 'Stores usage limits for users and organizations';
COMMENT ON TABLE usage_rate_tracking IS 'Tracks requests per minute for rate limiting';
COMMENT ON TABLE usage_daily_summary IS 'Daily aggregation of usage for daily limits';
COMMENT ON FUNCTION increment_daily_usage IS 'Atomically increments daily usage counters';
COMMENT ON FUNCTION increment_rate_tracking IS 'Atomically increments rate tracking and returns current count';
