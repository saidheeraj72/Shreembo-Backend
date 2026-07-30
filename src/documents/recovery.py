"""Reconcile documents left mid-processing by a previous process.

Embedding runs as an in-process background task, so a restart, crash or
``--reload`` takes every in-flight document with it. Nothing else ever revisits
those rows: they keep ``processing_status='processing'`` forever, the upload
websocket never emits a terminal event, and the repository UI polls them
indefinitely while showing "Processing".

On startup no such task can still be alive — this process just began and
embedding state is never shared between processes — so anything still marked
"processing" is stale by definition and is marked failed.

This assumes a single application process owns the table, which matches how the
service is deployed (one uvicorn worker; see the Dockerfile CMD). Running
several workers would need a claim/heartbeat column instead, so that one
worker's startup does not fail another's live work.
"""
import logging

from src.core.database import db

logger = logging.getLogger(__name__)


async def fail_orphaned_documents() -> int:
    """Mark documents stranded mid-processing as failed. Returns the count."""
    try:
        stale = (
            db.admin.table("storage_nodes")
            .select("id")
            .eq("node_type", "file")
            .eq("processing_status", "processing")
            .execute()
        )
        # .data is typed as a sequence of arbitrary JSON, so pull the ids out
        # defensively rather than assuming every element is a dict with an id.
        # An empty list here would make the .in_() below match nothing, but an
        # unguarded r["id"] would raise and abort startup housekeeping.
        ids = [
            row["id"]
            for row in (stale.data or [])
            if isinstance(row, dict) and row.get("id")
        ]
        if not ids:
            return 0

        db.admin.table("storage_nodes").update(
            {"processing_status": "failed", "embedding_status": "failed"}
        ).in_("id", ids).execute()

        logger.warning(
            "Marked %d document(s) failed — left mid-processing by a previous run",
            len(ids),
        )
        return len(ids)
    except Exception as e:
        # Never block startup over housekeeping.
        logger.error("Could not reconcile orphaned documents: %s", e)
        return 0
