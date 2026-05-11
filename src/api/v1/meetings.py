"""
Meeting Notes API routes.
Handles meeting lifecycle: create, record audio, transcribe, generate notes.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from src.core.dependencies import get_current_user_id, get_current_org_context
from src.meeting.service import meeting_service
from src.models.meeting import (
    AudioChunkResponse,
    MeetingCreateRequest,
    MeetingDetailResponse,
    MeetingEndRequest,
    MeetingNotesResponse,
    MeetingResponse,
    MeetingSearchRequest,
    MeetingUpdateRequest,
    TranscriptEntry,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/",
    response_model=MeetingResponse,
    summary="Create a new meeting",
    status_code=status.HTTP_201_CREATED,
)
async def create_meeting(
    request: MeetingCreateRequest,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context),
):
    """Create a new meeting session and start recording."""
    try:
        meeting = await meeting_service.create_meeting(
            user_id=user_id,
            org_id=org_context.get("org_id"),
            title=request.title,
            participant_count=request.participant_count,
            tags=request.tags,
        )
        return meeting
    except Exception as e:
        logger.exception("Failed to create meeting: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create meeting.",
        )


@router.get(
    "/",
    response_model=list[MeetingResponse],
    summary="List user's meetings",
)
async def list_meetings(
    limit: int = 20,
    offset: int = 0,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context),
):
    """Get all meetings for the current user."""
    try:
        meetings = await meeting_service.get_meetings(
            user_id=user_id,
            org_id=org_context.get("org_id"),
            limit=limit,
            offset=offset,
        )
        return meetings
    except Exception as e:
        logger.exception("Failed to list meetings: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list meetings.",
        )


@router.get(
    "/{meeting_id}",
    response_model=MeetingDetailResponse,
    summary="Get meeting details with transcript and notes",
)
async def get_meeting(
    meeting_id: str,
    user_id: UUID = Depends(get_current_user_id),
):
    """Get a meeting with its full transcript and generated notes."""
    meeting = await meeting_service.get_meeting(user_id, meeting_id)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found.",
        )

    # Fetch transcript chunks
    transcript = await meeting_service.get_transcript(meeting_id)

    # Parse notes from JSONB
    notes = meeting.get("notes")
    if isinstance(notes, str):
        import json
        try:
            notes = json.loads(notes)
        except (json.JSONDecodeError, TypeError):
            notes = None

    return {
        **meeting,
        "transcript": transcript,
        "notes": notes,
    }


@router.post(
    "/{meeting_id}/audio",
    response_model=AudioChunkResponse,
    summary="Process audio chunk",
)
async def process_audio(
    meeting_id: str,
    audio: UploadFile = File(...),
    user_id: UUID = Depends(get_current_user_id),
):
    """Upload and transcribe an audio chunk during recording."""
    try:
        audio_bytes = await audio.read()
        result = await meeting_service.transcribe_audio(
            user_id=user_id,
            meeting_id=meeting_id,
            audio_bytes=audio_bytes,
            filename=audio.filename or "chunk.webm",
            content_type=audio.content_type or "audio/webm",
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Audio processing failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process audio chunk.",
        )


@router.post(
    "/{meeting_id}/end",
    response_model=MeetingDetailResponse,
    summary="End meeting and generate notes",
)
async def end_meeting(
    meeting_id: str,
    request: MeetingEndRequest,
    user_id: UUID = Depends(get_current_user_id),
):
    """End a recording session and optionally generate structured notes."""
    try:
        meeting = await meeting_service.end_meeting(
            user_id=user_id,
            meeting_id=meeting_id,
            duration=request.duration,
            generate_notes=request.generate_notes,
        )

        # Fetch transcript for response
        transcript = await meeting_service.get_transcript(meeting_id)
        notes = meeting.get("notes")
        if isinstance(notes, str):
            import json
            try:
                notes = json.loads(notes)
            except (json.JSONDecodeError, TypeError):
                notes = None

        return {**meeting, "transcript": transcript, "notes": notes}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Failed to end meeting: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to end meeting.",
        )


@router.post(
    "/{meeting_id}/notes",
    response_model=MeetingDetailResponse,
    summary="Regenerate meeting notes",
)
async def regenerate_notes(
    meeting_id: str,
    user_id: UUID = Depends(get_current_user_id),
):
    """Regenerate structured notes from existing transcript."""
    try:
        meeting = await meeting_service.regenerate_notes(user_id, meeting_id)
        transcript = await meeting_service.get_transcript(meeting_id)
        notes = meeting.get("notes")
        if isinstance(notes, str):
            import json
            try:
                notes = json.loads(notes)
            except (json.JSONDecodeError, TypeError):
                notes = None
        return {**meeting, "transcript": transcript, "notes": notes}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Note regeneration failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to regenerate notes.",
        )


@router.patch(
    "/{meeting_id}",
    response_model=MeetingResponse,
    summary="Update meeting",
)
async def update_meeting(
    meeting_id: str,
    request: MeetingUpdateRequest,
    user_id: UUID = Depends(get_current_user_id),
):
    """Update meeting title, tags, or notes."""
    updates = request.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update.",
        )

    meeting = await meeting_service.update_meeting(user_id, meeting_id, updates)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found.",
        )
    return meeting


@router.delete(
    "/{meeting_id}",
    summary="Delete meeting",
    status_code=status.HTTP_200_OK,
)
async def delete_meeting(
    meeting_id: str,
    user_id: UUID = Depends(get_current_user_id),
):
    """Soft delete a meeting."""
    deleted = await meeting_service.delete_meeting(user_id, meeting_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found.",
        )
    return {"message": "Meeting deleted successfully."}


@router.post(
    "/search",
    response_model=list[MeetingResponse],
    summary="Search meetings",
)
async def search_meetings(
    request: MeetingSearchRequest,
    user_id: UUID = Depends(get_current_user_id),
    org_context: dict = Depends(get_current_org_context),
):
    """Search meetings by transcript content."""
    try:
        meetings = await meeting_service.search_meetings(
            user_id=user_id,
            org_id=org_context.get("org_id"),
            query=request.query,
            limit=request.limit,
            offset=request.offset,
        )
        return meetings
    except Exception as e:
        logger.exception("Meeting search failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search failed.",
        )
