-- Remove the email issues scan cache. Issue scans are now computed on demand
-- and never persisted (see src/email_agent/issues.py).
DROP TABLE IF EXISTS public.email_issue_scans;
