"""Embedding search operations."""
from typing import List, Optional
from uuid import UUID

from src.core.database import db
from src.core.openai_client import openai_client
from src.core.qdrant_client import qdrant_client


class EmbeddingSearchMixin:
    @staticmethod
    async def search(
        query: str,
        org_id: Optional[UUID],
        user_id: UUID,
        top_k: int = 10,
        folder_id: Optional[UUID] = None,
    ) -> List[dict]:
        query_embedding = await openai_client.get_embedding(query)
        filter_dict = {"folder_id": str(folder_id)} if folder_id else None
        namespace = str(org_id) if org_id else str(user_id)
        results = await qdrant_client.query(query_embedding, namespace, top_k, filter_dict)

        doc_ids = list(set(r.metadata.get("document_id") for r in results if r.metadata))
        if not doc_ids:
            return []

        docs = db.admin.table("storage_nodes").select("*").in_("id", doc_ids).execute()
        doc_map = {d["id"]: d for d in docs.data}

        return [
            {
                "score": r.score,
                "document": doc_map.get(r.metadata["document_id"]),
                "chunk_text": r.metadata.get("chunk_text", ""),
            }
            for r in results
            if r.metadata.get("document_id") in doc_map
        ]
