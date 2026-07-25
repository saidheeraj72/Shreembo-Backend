#!/usr/bin/env python3
"""
Rebuild the document vector index.

Needed after the parsing/chunking/hybrid-retrieval changes: existing vectors
carry the old chunk text, guessed page numbers, and no sparse vector, so hybrid
search stays off until the collection is recreated.

The main collection is dropped and recreated (this is what adds the sparse
vector config), then every active document is re-extracted and re-embedded.

Usage:
    python scripts/reindex_documents.py --dry-run       # report what would run
    python scripts/reindex_documents.py                 # reindex everything
    python scripts/reindex_documents.py --org <org_id>  # limit to one org
"""
import argparse
import asyncio
import os
import sys
from uuid import UUID

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config import settings                      # noqa: E402
from src.core.database import db                     # noqa: E402
from src.core.qdrant_client import qdrant_client     # noqa: E402
from src.llm.embedding import embedding_service      # noqa: E402


def fetch_documents(org_id: str = None) -> list:
    query = (
        db.admin.table("storage_nodes")
        .select("id, name, org_id, owner_id, parent_id, s3_key, file_extension")
        .eq("node_type", "file")
        .eq("status", "active")
        .not_.is_("s3_key", "null")
    )
    if org_id:
        query = query.eq("org_id", org_id)
    return query.execute().data or []


def recreate_collection():
    """Drop and recreate the main collection so it gains the sparse config."""
    name = settings.QDRANT_MAIN_COLLECTION
    existing = [c.name for c in qdrant_client.client.get_collections().collections]
    if name in existing:
        qdrant_client.client.delete_collection(collection_name=name)
        print(f"Dropped collection '{name}'")
    qdrant_client._ensured_collections.discard(name)
    qdrant_client._ensure_collection(name)
    print(f"Recreated '{name}' (hybrid: {qdrant_client.supports_hybrid()})")


async def reindex(org_id: str = None, dry_run: bool = False):
    docs = fetch_documents(org_id)
    supported = set(settings.SUPPORTED_EMBEDDING_TYPES)
    eligible = [d for d in docs if (d.get("file_extension") or "").lower() in supported]

    print(f"{len(docs)} active documents, {len(eligible)} with a supported file type")

    if dry_run:
        for d in eligible[:20]:
            print(f"  would reindex: {d['name']}")
        if len(eligible) > 20:
            print(f"  ... and {len(eligible) - 20} more")
        return

    if not eligible:
        print("Nothing to do.")
        return

    recreate_collection()

    ok = failed = 0
    for i, doc in enumerate(eligible, 1):
        try:
            await embedding_service.process_document(
                document_id=UUID(doc["id"]),
                org_id=UUID(doc["org_id"]) if doc.get("org_id") else None,
                s3_key=doc["s3_key"],
                file_type=(doc.get("file_extension") or "").lower(),
                user_id=str(doc["owner_id"]),
                upload_id=f"reindex-{doc['id']}",
                document_name=doc["name"],
                folder_id=doc.get("parent_id"),
            )
            ok += 1
        except Exception as e:
            failed += 1
            print(f"  FAILED {doc['name']}: {e}")
        print(f"[{i}/{len(eligible)}] {doc['name']}", flush=True)

    print(f"\nDone — {ok} reindexed, {failed} failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild the document vector index")
    parser.add_argument("--org", help="Limit to a single organization id")
    parser.add_argument("--dry-run", action="store_true", help="Report without changing anything")
    args = parser.parse_args()

    asyncio.run(reindex(org_id=args.org, dry_run=args.dry_run))
