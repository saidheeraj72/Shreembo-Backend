-- ==========================================
-- MEETING NOTES FEATURE
-- Tables for Granola-style meeting transcription & notes
-- ==========================================

-- Meeting status enum
CREATE TYPE meeting_status AS ENUM ('recording', 'processing', 'completed', 'failed');

-- Meetings table
CREATE TABLE public.meetings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES public.organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    title TEXT NOT NULL,
    status meeting_status DEFAULT 'recording',
    duration INTEGER DEFAULT 0,
    participant_count INTEGER DEFAULT 1,
    tags TEXT[] DEFAULT '{}',

    -- Denormalized full transcript for search
    transcript_text TEXT DEFAULT '',

    -- Structured notes (Granola template as JSONB)
    -- Schema: { summary, key_decisions, action_items, follow_ups, key_topics }
    notes JSONB DEFAULT NULL,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

-- Indexes for meetings
CREATE INDEX idx_meetings_user ON public.meetings(user_id);
CREATE INDEX idx_meetings_org ON public.meetings(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_meetings_status ON public.meetings(status);
CREATE INDEX idx_meetings_created ON public.meetings(created_at DESC);
CREATE INDEX idx_meetings_search ON public.meetings USING gin(transcript_text gin_trgm_ops);

-- Transcript chunks table (normalized for efficient appends)
CREATE TABLE public.meeting_transcript_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    meeting_id UUID NOT NULL REFERENCES public.meetings(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    timestamp_seconds FLOAT NOT NULL,
    text TEXT NOT NULL,
    speaker TEXT DEFAULT NULL,
    is_final BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_transcript_meeting ON public.meeting_transcript_chunks(meeting_id, chunk_index);

-- RLS Policies
ALTER TABLE public.meetings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.meeting_transcript_chunks ENABLE ROW LEVEL SECURITY;

-- Users can only see their own meetings
CREATE POLICY "Users can view own meetings"
    ON public.meetings FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own meetings"
    ON public.meetings FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own meetings"
    ON public.meetings FOR UPDATE
    USING (auth.uid() = user_id);

-- Transcript chunks follow meeting ownership
CREATE POLICY "Users can view own transcript chunks"
    ON public.meeting_transcript_chunks FOR SELECT
    USING (meeting_id IN (SELECT id FROM public.meetings WHERE user_id = auth.uid()));

CREATE POLICY "Users can insert own transcript chunks"
    ON public.meeting_transcript_chunks FOR INSERT
    WITH CHECK (meeting_id IN (SELECT id FROM public.meetings WHERE user_id = auth.uid()));
