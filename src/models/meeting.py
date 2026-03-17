"""
Pydantic models for Meeting Transcriber module.
"""
from typing import Optional
from enum import Enum
from pydantic import BaseModel, Field


class MeetingStatus(str, Enum):
    RECORDING = "recording"
    PROCESSING = "processing"
    COMPLETED = "completed"


class MeetingCreateRequest(BaseModel):
    """Request body for creating a new meeting."""
    title: str = Field(..., min_length=1, max_length=500, description="Meeting title")


class MeetingResponse(BaseModel):
    """Response body for a meeting."""
    id: str
    title: str
    status: MeetingStatus
    duration: int = 0
    created_at: str
    updated_at: str


class TranscriptEntry(BaseModel):
    """A single transcript entry."""
    id: str
    timestamp: float
    text: str
    speaker: Optional[str] = None
    is_final: bool = True


class MeetingNotesResponse(BaseModel):
    """AI-generated meeting notes."""
    summary: str
    key_points: list[str]
    action_items: list[str]
    decisions: list[str]


class MeetingDetailResponse(MeetingResponse):
    """Full meeting detail with transcript and notes."""
    transcript: list[TranscriptEntry] = []
    notes: Optional[MeetingNotesResponse] = None


class AudioChunkResponse(BaseModel):
    """Response after processing an audio chunk."""
    transcript_chunk: str


class MeetingEndRequest(BaseModel):
    """Optional payload when ending a meeting."""
    duration: Optional[int] = None
    transcript: Optional[list[TranscriptEntry]] = None
