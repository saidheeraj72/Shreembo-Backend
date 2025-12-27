-- ==========================================
-- Migration: Add Personal User Subscription Fields
-- Description: Add subscription tracking fields to profiles table for personal accounts
-- Date: 2025-12-27
-- ==========================================

-- Add subscription fields to profiles table
ALTER TABLE public.profiles
ADD COLUMN IF NOT EXISTS plan_type plan_type DEFAULT 'free',
ADD COLUMN IF NOT EXISTS subscription_status subscription_status DEFAULT 'trial',
ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS subscription_ends_at TIMESTAMPTZ;

-- Add index for subscription status queries
CREATE INDEX IF NOT EXISTS idx_profiles_subscription_status
ON public.profiles(subscription_status)
WHERE account_type = 'personal';

-- Add index for trial expiry checks
CREATE INDEX IF NOT EXISTS idx_profiles_trial_ends
ON public.profiles(trial_ends_at)
WHERE trial_ends_at IS NOT NULL AND account_type = 'personal';

-- Add comment
COMMENT ON COLUMN public.profiles.plan_type IS 'Subscription plan for personal accounts (free, starter, professional, business, enterprise)';
COMMENT ON COLUMN public.profiles.subscription_status IS 'Subscription status for personal accounts (active, trial, expired, cancelled, past_due)';
COMMENT ON COLUMN public.profiles.trial_ends_at IS 'Trial expiration date for personal accounts';
COMMENT ON COLUMN public.profiles.subscription_ends_at IS 'Subscription expiration date for personal accounts';

-- Update existing personal users with 14-day trial
UPDATE public.profiles
SET
    trial_ends_at = NOW() + INTERVAL '14 days',
    subscription_status = 'trial'
WHERE
    account_type = 'personal'
    AND trial_ends_at IS NULL;

COMMENT ON TABLE public.profiles IS 'User profiles linked to Supabase auth.users. Personal accounts have subscription fields, organization members inherit from organizations table.';
