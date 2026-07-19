-- Bounce Board v3 — session copilot chat.
-- Apply manually in the Supabase SQL editor (this repo has no migration runner).
--
--   copilot_chat : the AI-chat conversation attached to a session. The copilot
--                  answers questions about the analysis and can modify its
--                  artifacts (problem statement, KPIs, recommendations, report
--                  sections) or rerun the pipeline from a stage.

ALTER TABLE public.bounce_board_sessions
    ADD COLUMN IF NOT EXISTS copilot_chat JSONB;
