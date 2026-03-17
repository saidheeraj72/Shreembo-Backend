"""
Meeting service — handles transcription via Whisper and note generation via GPT.
"""
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from openai import AsyncOpenAI

from src.config import settings
from src.core.database import db
from src.llm.token_usage import token_usage_service

logger = logging.getLogger(__name__)

NOTES_SYSTEM_PROMPT = """You are an expert meeting analyst. Given a meeting transcript, generate structured meeting notes in Granola style.

Respond in valid JSON with exactly these keys:
{
  "summary": "2-4 sentence executive summary of the meeting",
  "key_decisions": ["Decision 1", "Decision 2"],
  "action_items": [
    {"text": "Action description", "assignee": "Person name or null", "due_date": "Date or null", "completed": false}
  ],
  "follow_ups": ["Follow-up item 1"],
  "key_topics": ["Topic 1", "Topic 2"]
}

Rules:
- summary: capture purpose, outcome, and next steps
- key_decisions: concrete decisions made, not discussion points
- action_items: specific tasks with responsible person if mentioned
- follow_ups: items that need future attention but aren't immediate tasks
- key_topics: main themes discussed (3-7 topics)
- Do not fabricate information not in the transcript
- Return empty arrays if no items found for a category"""


class MeetingService:
    """Handles meeting lifecycle: creation, transcription, and note generation."""

    def __init__(self):
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return self._client

    # --- CRUD ---

    async def create_meeting(
        self, user_id: UUID, org_id: Optional[UUID], title: str,
        participant_count: int = 1, tags: list[str] | None = None,
    ) -> dict:
        data = {
            "user_id": str(user_id),
            "org_id": str(org_id) if org_id else None,
            "title": title,
            "status": "recording",
            "participant_count": participant_count,
            "tags": tags or [],
        }
        result = db.admin.table("meetings").insert(data).execute()
        return result.data[0]

    async def get_meetings(
        self, user_id: UUID, org_id: Optional[UUID] = None,
        limit: int = 20, offset: int = 0,
    ) -> list[dict]:
        query = (
            db.admin.table("meetings")
            .select("*")
            .eq("user_id", str(user_id))
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if org_id:
            query = query.eq("org_id", str(org_id))
        result = query.execute()
        return result.data or []

    async def get_meeting(self, user_id: UUID, meeting_id: str) -> dict | None:
        result = (
            db.admin.table("meetings")
            .select("*")
            .eq("id", meeting_id)
            .eq("user_id", str(user_id))
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
        return result.data

    async def get_transcript(self, meeting_id: str) -> list[dict]:
        result = (
            db.admin.table("meeting_transcript_chunks")
            .select("*")
            .eq("meeting_id", meeting_id)
            .order("chunk_index", desc=False)
            .execute()
        )
        return result.data or []

    async def update_meeting(self, user_id: UUID, meeting_id: str, updates: dict) -> dict | None:
        updates["updated_at"] = datetime.utcnow().isoformat()
        result = (
            db.admin.table("meetings")
            .update(updates)
            .eq("id", meeting_id)
            .eq("user_id", str(user_id))
            .execute()
        )
        return result.data[0] if result.data else None

    async def delete_meeting(self, user_id: UUID, meeting_id: str) -> bool:
        result = (
            db.admin.table("meetings")
            .update({"deleted_at": datetime.utcnow().isoformat()})
            .eq("id", meeting_id)
            .eq("user_id", str(user_id))
            .execute()
        )
        return bool(result.data)

    async def search_meetings(
        self, user_id: UUID, org_id: Optional[UUID],
        query: str, limit: int = 20, offset: int = 0,
    ) -> list[dict]:
        q = (
            db.admin.table("meetings")
            .select("*")
            .eq("user_id", str(user_id))
            .is_("deleted_at", "null")
            .ilike("transcript_text", f"%{query}%")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if org_id:
            q = q.eq("org_id", str(org_id))
        result = q.execute()
        return result.data or []

    # --- Transcription ---

    async def transcribe_audio(
        self, user_id: UUID, meeting_id: str,
        audio_bytes: bytes, filename: str, content_type: str,
    ) -> dict:
        """Transcribe an audio chunk using OpenAI Whisper and store the result."""
        # Verify meeting ownership
        meeting = await self.get_meeting(user_id, meeting_id)
        if not meeting:
            raise ValueError("Meeting not found")
        if meeting["status"] != "recording":
            # Late chunks can arrive while end-meeting transitions status to
            # processing/completed; ignore these instead of failing the request.
            return {"transcript_chunk": "", "chunk_index": -1}

        # Skip very small chunks (likely silence)
        if len(audio_bytes) < 1000:
            return {"transcript_chunk": "", "chunk_index": -1}

        # Get next chunk index
        existing = (
            db.admin.table("meeting_transcript_chunks")
            .select("chunk_index")
            .eq("meeting_id", meeting_id)
            .order("chunk_index", desc=True)
            .limit(1)
            .execute()
        )
        next_index = (existing.data[0]["chunk_index"] + 1) if existing.data else 0

        # Determine file extension from content type
        ext_map = {
            "audio/webm": ".webm",
            "audio/webm;codecs=opus": ".webm",
            "audio/ogg": ".ogg",
            "audio/ogg;codecs=opus": ".ogg",
            "audio/mp4": ".mp4",
            "audio/mpeg": ".mp3",
        }
        ext = ext_map.get(content_type, ".webm")
        upload_filename = filename or f"chunk{ext}"
        if "." not in upload_filename:
            upload_filename = f"{upload_filename}{ext}"

        try:
            transcription = await self.client.audio.transcriptions.create(
                model="whisper-1",
                file=(upload_filename, audio_bytes, content_type),
                response_format="text",
            )
        except Exception as e:
            logger.error("Whisper transcription failed: %s", e)
            return {"transcript_chunk": "", "chunk_index": next_index}

        transcript_text = transcription.strip() if isinstance(transcription, str) else str(transcription).strip()

        if not transcript_text:
            return {"transcript_chunk": "", "chunk_index": next_index}

        # Calculate approximate timestamp
        timestamp_seconds = next_index * 5.0  # 5 second chunks

        # Store transcript chunk
        db.admin.table("meeting_transcript_chunks").insert({
            "meeting_id": meeting_id,
            "chunk_index": next_index,
            "timestamp_seconds": timestamp_seconds,
            "text": transcript_text,
        }).execute()

        return {"transcript_chunk": transcript_text, "chunk_index": next_index}

    # --- End Meeting & Note Generation ---

    async def end_meeting(
        self, user_id: UUID, meeting_id: str,
        duration: int | None = None, generate_notes: bool = True,
    ) -> dict:
        """End a meeting: build full transcript, optionally generate notes."""
        meeting = await self.get_meeting(user_id, meeting_id)
        if not meeting:
            raise ValueError("Meeting not found")

        # Set status to processing
        await self.update_meeting(user_id, meeting_id, {"status": "processing"})

        # Build full transcript text from chunks
        chunks = await self.get_transcript(meeting_id)
        full_transcript = " ".join(c["text"] for c in chunks if c["text"])

        updates: dict = {
            "transcript_text": full_transcript,
            "status": "processing",
        }
        if duration is not None:
            updates["duration"] = duration

        await self.update_meeting(user_id, meeting_id, updates)

        # Generate notes if requested and there's transcript content
        if generate_notes and full_transcript.strip():
            try:
                notes = await self._generate_notes(
                    user_id=user_id,
                    org_id=UUID(meeting["org_id"]) if meeting.get("org_id") else None,
                    transcript=full_transcript,
                )
                await self.update_meeting(user_id, meeting_id, {
                    "notes": json.dumps(notes) if isinstance(notes, dict) else notes,
                    "status": "completed",
                })
            except Exception as e:
                logger.exception("Note generation failed for meeting %s: %s", meeting_id, e)
                await self.update_meeting(user_id, meeting_id, {"status": "failed"})
        else:
            await self.update_meeting(user_id, meeting_id, {"status": "completed"})

        return await self.get_meeting(user_id, meeting_id)

    async def regenerate_notes(self, user_id: UUID, meeting_id: str) -> dict:
        """Regenerate notes for an existing meeting."""
        meeting = await self.get_meeting(user_id, meeting_id)
        if not meeting:
            raise ValueError("Meeting not found")

        transcript = meeting.get("transcript_text", "")
        if not transcript.strip():
            raise ValueError("No transcript available to generate notes from")

        await self.update_meeting(user_id, meeting_id, {"status": "processing"})

        try:
            notes = await self._generate_notes(
                user_id=user_id,
                org_id=UUID(meeting["org_id"]) if meeting.get("org_id") else None,
                transcript=transcript,
            )
            await self.update_meeting(user_id, meeting_id, {
                "notes": json.dumps(notes) if isinstance(notes, dict) else notes,
                "status": "completed",
            })
        except Exception as e:
            logger.exception("Note regeneration failed for meeting %s: %s", meeting_id, e)
            await self.update_meeting(user_id, meeting_id, {"status": "failed"})
            raise

        return await self.get_meeting(user_id, meeting_id)

    async def _generate_notes(
        self, user_id: UUID, org_id: Optional[UUID], transcript: str,
    ) -> dict:
        """Generate structured meeting notes from transcript using GPT."""
        # Truncate very long transcripts to fit context window
        max_chars = 100_000
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars] + "\n\n[Transcript truncated due to length]"

        response = await self.client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": NOTES_SYSTEM_PROMPT},
                {"role": "user", "content": f"Generate structured meeting notes from this transcript:\n\n{transcript}"},
            ],
            max_tokens=settings.OPENAI_CHAT_MAX_TOKENS,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        # Track token usage
        if response.usage:
            await token_usage_service.track_usage(
                user_id=user_id,
                org_id=org_id,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )

        content = response.choices[0].message.content or "{}"
        return json.loads(content)


# Module-level singleton
meeting_service = MeetingService()
