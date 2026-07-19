-- Bounce Board v2 — dynamic board composition + real attachments.
-- Apply manually in the Supabase SQL editor (this repo has no migration runner).
--
--   board       : the LLM-composed expert board (expert, personas, rounds,
--                 selected frameworks) persisted per session so the frontend
--                 renders the real personas instead of fixtures.
--   attachments : uploaded session documents with their extracted text
--                 (capped), consumed by the intake/context/framework stages.

ALTER TABLE public.bounce_board_sessions
    ADD COLUMN IF NOT EXISTS board JSONB,
    ADD COLUMN IF NOT EXISTS attachments JSONB;
