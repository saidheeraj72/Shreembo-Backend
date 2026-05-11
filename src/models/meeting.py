"""
Meeting models for Granola-style meeting transcription & notes.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class MeetingStatus(str, Enum):
    RECORDING = "recording"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# --- Request Models ---

class MeetingCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    participant_count: int = Field(default=1, ge=1)
    tags: list[str] = Field(default_factory=list)


class MeetingEndRequest(BaseModel):
    duration: Optional[int] = None
    generate_notes: bool = True


class MeetingUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    tags: Optional[list[str]] = None
    notes: Optional[dict] = None  # Allow manual note edits


class MeetingSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


# --- Response Models ---

class ActionItem(BaseModel):
    text: str
    assignee: Optional[str] = None
    due_date: Optional[str] = None
    completed: bool = False


class MeetingNotesResponse(BaseModel):
    summary: str = ""
    key_decisions: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    key_topics: list[str] = Field(default_factory=list)


class TranscriptEntry(BaseModel):
    id: str
    chunk_index: int
    timestamp_seconds: float
    text: str
    speaker: Optional[str] = None
    is_final: bool = True


class MeetingResponse(BaseModel):
    id: str
    title: str
    status: MeetingStatus
    duration: int = 0
    participant_count: int = 1
    tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class MeetingDetailResponse(MeetingResponse):
    transcript: list[TranscriptEntry] = Field(default_factory=list)
    notes: Optional[MeetingNotesResponse] = None


class AudioChunkResponse(BaseModel):
    transcript_chunk: str
    chunk_index: int
