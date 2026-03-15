"""
Meeting Transcriber service — handles audio transcription and note generation.
Uses OpenAI Whisper for transcription and GPT for note generation.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI, BadRequestError

from src.config import settings
from src.core.database import db

logger = logging.getLogger(__name__)

NOTES_SYSTEM_PROMPT = """You are an expert meeting analyst. Given a meeting transcript, generate structured meeting notes.

You MUST respond in valid JSON format with exactly these keys:
{
  "summary": "A concise 2-3 sentence summary of the meeting",
  "key_points": ["Key point 1", "Key point 2", ...],
  "action_items": ["Action item 1", "Action item 2", ...],
  "decisions": ["Decision 1", "Decision 2", ...]
}

Rules:
- Summary should capture the main purpose and outcome of the meeting
- Key points should be the most important topics discussed
- Action items should be specific, actionable tasks mentioned (include who is responsible if mentioned)
- Decisions should be concrete decisions that were made during the meeting
- If no action items or decisions were explicitly made, return empty arrays
- Keep each point concise but informative
- Do not fabricate information not present in the transcript"""


class MeetingService:
    """Handles meeting transcription and AI note generation."""

    def __init__(self):
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return self._client

    async def create_meeting(self, user_id: str, title: str) -> dict:
        """Create a new meeting session."""
        now = datetime.now(timezone.utc).isoformat()
        meeting_data = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "title": title,
            "status": "recording",
            "duration": 0,
            "transcript": [],
            "notes": None,
            "created_at": now,
            "updated_at": now,
        }

        response = (
            db.admin.table("meetings")
            .insert(meeting_data)
            .execute()
        )

        return response.data[0] if response.data else meeting_data

    async def get_meetings(self, user_id: str, limit: int = 20, offset: int = 0) -> list[dict]:
        """Get user's meetings."""
        response = (
            db.admin.table("meetings")
            .select("id, title, status, duration, created_at, updated_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return response.data or []

    async def get_meeting(self, user_id: str, meeting_id: str) -> Optional[dict]:
        """Get a single meeting with full details."""
        response = (
            db.admin.table("meetings")
            .select("*")
            .eq("id", meeting_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    async def end_meeting(
        self,
        user_id: str,
        meeting_id: str,
        duration: Optional[int] = None,
        transcript: Optional[list[dict]] = None,
    ) -> Optional[dict]:
        """Mark a meeting as completed."""
        meeting = await self.get_meeting(user_id, meeting_id)
        if not meeting:
            return None

        now = datetime.now(timezone.utc).isoformat()
        update_payload: dict = {"status": "completed", "updated_at": now}

        if isinstance(duration, int) and duration >= 0:
            update_payload["duration"] = duration

        existing_transcript = meeting.get("transcript") or []
        incoming_transcript = transcript or []
        # Use client transcript as fallback if backend transcript is empty.
        if not existing_transcript and incoming_transcript:
            update_payload["transcript"] = incoming_transcript

        response = (
            db.admin.table("meetings")
            .update(update_payload)
            .eq("id", meeting_id)
            .eq("user_id", user_id)
            .execute()
        )
        return response.data[0] if response.data else None

    async def delete_meeting(self, user_id: str, meeting_id: str) -> bool:
        """Delete a meeting."""
        response = (
            db.admin.table("meetings")
            .delete()
            .eq("id", meeting_id)
            .eq("user_id", user_id)
            .execute()
        )
        return bool(response.data)

    @staticmethod
    def _infer_audio_upload_meta(filename: str, content_type: Optional[str]) -> tuple[str, str]:
        """Infer a stable filename/content-type pair for transcription uploads."""
        filename_l = (filename or "").lower()
        content_type_l = (content_type or "").lower()

        # Prefer explicit container types from content-type, then filename hints.
        if "ogg" in content_type_l or filename_l.endswith((".ogg", ".oga")):
            return "chunk.ogg", "audio/ogg"
        if "wav" in content_type_l or filename_l.endswith(".wav"):
            return "chunk.wav", "audio/wav"
        if "mpeg" in content_type_l or filename_l.endswith((".mp3", ".mpga", ".mpeg")):
            return "chunk.mp3", "audio/mpeg"
        if "mp4" in content_type_l or "m4a" in content_type_l or filename_l.endswith((".mp4", ".m4a")):
            return "chunk.m4a", "audio/mp4"
        if "webm" in content_type_l or filename_l.endswith(".webm"):
            return "chunk.webm", "audio/webm"
        if "flac" in content_type_l or filename_l.endswith(".flac"):
            return "chunk.flac", "audio/flac"

        # Safe default for browser MediaRecorder audio.
        return "chunk.webm", "audio/webm"

    async def transcribe_audio(
        self,
        user_id: str,
        meeting_id: str,
        audio_bytes: bytes,
        filename: str,
        content_type: Optional[str] = None,
    ) -> str:
        """Transcribe an audio chunk using OpenAI Whisper."""
        # Skip chunks that are too small (no meaningful audio)
        if len(audio_bytes) < 1000:
            logger.warning("Audio chunk too small (%d bytes), skipping", len(audio_bytes))
            return ""

        upload_filename, upload_content_type = self._infer_audio_upload_meta(filename, content_type)
        try:
            transcript = await self.client.audio.transcriptions.create(
                model="whisper-1",
                file=(upload_filename, audio_bytes, upload_content_type),
                response_format="text",
            )
        except BadRequestError as e:
            error_text = str(e)
            if "Invalid file format" in error_text:
                logger.warning(
                    "Skipping malformed audio chunk for meeting %s (filename=%s, content_type=%s, size=%d)",
                    meeting_id,
                    filename,
                    content_type,
                    len(audio_bytes),
                )
                return ""
            raise

        transcript_text = transcript.strip() if isinstance(transcript, str) else str(transcript).strip()

        if not transcript_text:
            return ""

        # Append to meeting transcript in DB
        meeting = await self.get_meeting(user_id, meeting_id)
        if meeting:
            existing_transcript = meeting.get("transcript") or []
            new_entry = {
                "id": str(uuid.uuid4()),
                "timestamp": len(existing_transcript) * 10.0,  # approximate timestamp
                "text": transcript_text,
                "is_final": True,
            }
            existing_transcript.append(new_entry)

            now = datetime.now(timezone.utc).isoformat()
            db.admin.table("meetings").update({
                "transcript": existing_transcript,
                "updated_at": now,
            }).eq("id", meeting_id).eq("user_id", user_id).execute()

        return transcript_text

    async def generate_notes(self, user_id: str, meeting_id: str) -> dict:
        """Generate AI meeting notes from the transcript."""
        meeting = await self.get_meeting(user_id, meeting_id)
        if not meeting:
            raise ValueError("Meeting not found")

        transcript_entries = meeting.get("transcript") or []
        if not transcript_entries:
            raise ValueError("No transcript available to generate notes from")

        # Build transcript text
        transcript_text = "\n".join(
            f"[{entry.get('timestamp', 0):.0f}s] {entry['text']}"
            for entry in transcript_entries
        )

        # Update status to processing
        now = datetime.now(timezone.utc).isoformat()
        db.admin.table("meetings").update({
            "status": "processing",
            "updated_at": now,
        }).eq("id", meeting_id).eq("user_id", user_id).execute()

        logger.info("Generating meeting notes for meeting %s", meeting_id)

        response = await self.client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": NOTES_SYSTEM_PROMPT},
                {"role": "user", "content": f"Generate meeting notes from this transcript:\n\n{transcript_text}"},
            ],
            max_tokens=settings.OPENAI_CHAT_MAX_TOKENS,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        notes_text = response.choices[0].message.content or "{}"
        notes = json.loads(notes_text)

        # Ensure all required fields exist
        notes = {
            "summary": notes.get("summary", ""),
            "key_points": notes.get("key_points", []),
            "action_items": notes.get("action_items", []),
            "decisions": notes.get("decisions", []),
        }

        # Save notes to DB
        now = datetime.now(timezone.utc).isoformat()
        db.admin.table("meetings").update({
            "notes": notes,
            "status": "completed",
            "updated_at": now,
        }).eq("id", meeting_id).eq("user_id", user_id).execute()

        return notes

    async def get_transcript(self, user_id: str, meeting_id: str) -> list[dict]:
        """Get the transcript for a meeting."""
        meeting = await self.get_meeting(user_id, meeting_id)
        if not meeting:
            raise ValueError("Meeting not found")
        return meeting.get("transcript") or []


# Module-level singleton
meeting_service = MeetingService()
