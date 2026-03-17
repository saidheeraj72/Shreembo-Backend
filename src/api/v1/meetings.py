"""
Meeting Transcriber API routes.
Handles meeting CRUD, audio transcription, and AI note generation.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status

from src.core.dependencies import get_current_user_id
from src.models.meeting import (
    MeetingCreateRequest,
    MeetingResponse,
    MeetingDetailResponse,
    MeetingNotesResponse,
    AudioChunkResponse,
    TranscriptEntry,
    MeetingEndRequest,
)
from src.meeting.service import meeting_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "",
    response_model=MeetingResponse,
    summary="Create a new meeting",
    status_code=status.HTTP_201_CREATED,
)
async def create_meeting(
    request: MeetingCreateRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> MeetingResponse:
    """Create a new meeting session for recording."""
    try:
        meeting = await meeting_service.create_meeting(str(user_id), request.title)
        return MeetingResponse(**meeting)
    except Exception as e:
        logger.exception("Failed to create meeting for user %s: %s", user_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create meeting.",
        )


@router.get(
    "",
    response_model=list[MeetingResponse],
    summary="List meetings",
)
async def list_meetings(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user_id),
) -> list[MeetingResponse]:
    """List user's meetings."""
    try:
        meetings = await meeting_service.get_meetings(str(user_id), limit, offset)
        return [MeetingResponse(**m) for m in meetings]
    except Exception as e:
        logger.exception("Failed to list meetings for user %s: %s", user_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list meetings.",
        )


@router.get(
    "/{meeting_id}",
    response_model=MeetingDetailResponse,
    summary="Get meeting details",
)
async def get_meeting(
    meeting_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> MeetingDetailResponse:
    """Get full meeting details including transcript and notes."""
    try:
        meeting = await meeting_service.get_meeting(str(user_id), meeting_id)
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Meeting not found.",
            )

        # Parse transcript entries
        transcript = meeting.get("transcript") or []
        transcript_entries = [TranscriptEntry(**t) for t in transcript]

        # Parse notes
        notes = None
        if meeting.get("notes"):
            notes = MeetingNotesResponse(**meeting["notes"])

        return MeetingDetailResponse(
            id=meeting["id"],
            title=meeting["title"],
            status=meeting["status"],
            duration=meeting.get("duration", 0),
            created_at=meeting["created_at"],
            updated_at=meeting["updated_at"],
            transcript=transcript_entries,
            notes=notes,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get meeting %s: %s", meeting_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get meeting.",
        )


@router.post(
    "/{meeting_id}/audio",
    response_model=AudioChunkResponse,
    summary="Send audio chunk for transcription",
)
async def send_audio_chunk(
    meeting_id: str,
    audio: UploadFile = File(...),
    user_id: UUID = Depends(get_current_user_id),
) -> AudioChunkResponse:
    """Upload an audio chunk and get back the transcription."""
    try:
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty audio file.",
            )

        transcript_chunk = await meeting_service.transcribe_audio(
            str(user_id),
            meeting_id,
            audio_bytes,
            audio.filename or "chunk.webm",
            audio.content_type,
        )

        return AudioChunkResponse(transcript_chunk=transcript_chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to transcribe audio for meeting %s: %s", meeting_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to transcribe audio.",
        )


@router.get(
    "/{meeting_id}/transcript",
    response_model=list[TranscriptEntry],
    summary="Get meeting transcript",
)
async def get_transcript(
    meeting_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> list[TranscriptEntry]:
    """Get the transcript for a meeting."""
    try:
        transcript = await meeting_service.get_transcript(str(user_id), meeting_id)
        return [TranscriptEntry(**t) for t in transcript]
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Failed to get transcript for meeting %s: %s", meeting_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get transcript.",
        )


@router.put(
    "/{meeting_id}/transcript",
    response_model=MeetingDetailResponse,
    summary="Save meeting transcript",
)
async def save_transcript(
    meeting_id: str,
    transcript: list[TranscriptEntry],
    user_id: UUID = Depends(get_current_user_id),
) -> MeetingDetailResponse:
    """Save the full transcript from the frontend to the database."""
    try:
        transcript_dicts = [
            {
                "id": entry.id,
                "timestamp": entry.timestamp,
                "text": entry.text,
                "speaker": entry.speaker,
                "is_final": entry.is_final,
            }
            for entry in transcript
        ]
        meeting = await meeting_service.save_transcript(
            str(user_id), meeting_id, transcript_dicts
        )
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Meeting not found.",
            )

        parsed_transcript = [TranscriptEntry(**t) for t in meeting.get("transcript") or []]
        notes = None
        if meeting.get("notes"):
            notes = MeetingNotesResponse(**meeting["notes"])

        return MeetingDetailResponse(
            id=meeting["id"],
            title=meeting["title"],
            status=meeting["status"],
            duration=meeting.get("duration", 0),
            created_at=meeting["created_at"],
            updated_at=meeting["updated_at"],
            transcript=parsed_transcript,
            notes=notes,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to save transcript for meeting %s: %s", meeting_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save transcript.",
        )


@router.post(
    "/{meeting_id}/notes",
    response_model=MeetingNotesResponse,
    summary="Generate AI meeting notes",
)
async def generate_notes(
    meeting_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> MeetingNotesResponse:
    """Generate AI-powered notes from the meeting transcript."""
    try:
        notes = await meeting_service.generate_notes(str(user_id), meeting_id)
        return MeetingNotesResponse(**notes)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Failed to generate notes for meeting %s: %s", meeting_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate meeting notes.",
        )


@router.post(
    "/{meeting_id}/end",
    response_model=MeetingResponse,
    summary="End a meeting",
)
async def end_meeting(
    meeting_id: str,
    request: MeetingEndRequest | None = None,
    user_id: UUID = Depends(get_current_user_id),
) -> MeetingResponse:
    """Mark a meeting as completed."""
    try:
        request = request or MeetingEndRequest()
        transcript = None
        if request.transcript:
            transcript = [
                {
                    "id": entry.id,
                    "timestamp": entry.timestamp,
                    "text": entry.text,
                    "speaker": entry.speaker,
                    "is_final": entry.is_final,
                }
                for entry in request.transcript
            ]

        meeting = await meeting_service.end_meeting(
            str(user_id),
            meeting_id,
            request.duration,
            transcript,
        )
        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Meeting not found.",
            )
        return MeetingResponse(**meeting)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to end meeting %s: %s", meeting_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to end meeting.",
        )


@router.delete(
    "/{meeting_id}",
    summary="Delete a meeting",
    status_code=status.HTTP_200_OK,
)
async def delete_meeting(
    meeting_id: str,
    user_id: UUID = Depends(get_current_user_id),
):
    """Delete a meeting and its data."""
    try:
        deleted = await meeting_service.delete_meeting(str(user_id), meeting_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Meeting not found.",
            )
        return {"detail": "Meeting deleted."}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete meeting %s: %s", meeting_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete meeting.",
        )
