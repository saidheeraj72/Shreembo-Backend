"""Qdrant client for vector embeddings (local file-based)."""
from typing import List, Dict, Optional
from dataclasses import dataclass
import uuid
import logging

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class QueryMatch:
    """Lightweight result object compatible with existing code that accesses .score and .metadata."""
    score: float
    metadata: Dict


class QdrantVectorClient:
    """Wrapper around qdrant-client for local file-based vector storage."""

    def __init__(self):
        self._client = None
        self._ensured_collections: set = set()

    @property
    def client(self):
        if self._client is None:
            from qdrant_client import QdrantClient as _QdrantClient
            self._client = _QdrantClient(path=settings.QDRANT_PATH)
        return self._client

    def _get_collection_name(self, index_name: Optional[str] = None) -> str:
        if index_name == settings.QDRANT_SESSIONS_COLLECTION:
            return settings.QDRANT_SESSIONS_COLLECTION
        return index_name or settings.QDRANT_MAIN_COLLECTION

    def _ensure_collection(self, collection_name: str):
        """Create collection if it doesn't exist."""
        if collection_name in self._ensured_collections:
            return
        try:
            from qdrant_client.models import Distance, VectorParams
            collections = [c.name for c in self.client.get_collections().collections]
            if collection_name not in collections:
                self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=settings.EMBEDDING_DIMENSIONS,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info("Created Qdrant collection: %s", collection_name)
            self._ensured_collections.add(collection_name)
        except Exception as e:
            logger.error("Failed to ensure collection %s: %s", collection_name, e)
            raise

    @staticmethod
    def _make_point_id(string_id: str) -> str:
        """Convert a string ID to a deterministic UUID for Qdrant point IDs."""
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, string_id))

    def _build_filter(self, namespace: str, extra_filter: Optional[Dict] = None):
        """Build a Qdrant filter combining namespace + optional metadata filters."""
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        conditions = [
            FieldCondition(key="namespace", match=MatchValue(value=namespace))
        ]

        if extra_filter:
            for key, value in extra_filter.items():
                if isinstance(value, dict) and "$in" in value:
                    from qdrant_client.models import MatchAny
                    conditions.append(
                        FieldCondition(key=key, match=MatchAny(any=value["$in"]))
                    )
                else:
                    conditions.append(
                        FieldCondition(key=key, match=MatchValue(value=value))
                    )

        return Filter(must=conditions)

    async def upsert(self, vectors: List[Dict], namespace: str, index_name: Optional[str] = None) -> bool:
        """Store vectors in Qdrant. Each vector dict has 'id', 'values', 'metadata'."""
        try:
            from qdrant_client.models import PointStruct

            collection = self._get_collection_name(index_name)
            self._ensure_collection(collection)

            points = []
            for v in vectors:
                payload = dict(v.get("metadata", {}))
                payload["namespace"] = namespace

                points.append(PointStruct(
                    id=self._make_point_id(v["id"]),
                    vector=v["values"],
                    payload=payload,
                ))

            self.client.upsert(collection_name=collection, points=points)
            return True
        except Exception as e:
            logger.error("Qdrant upsert error: %s", e)
            return False

    async def query(
        self,
        vector: List[float],
        namespace: str,
        top_k: int = 10,
        filter: Optional[Dict] = None,
        index_name: Optional[str] = None,
    ) -> List[QueryMatch]:
        """Search vectors. Returns list of QueryMatch with .score and .metadata."""
        try:
            collection = self._get_collection_name(index_name)
            self._ensure_collection(collection)

            qdrant_filter = self._build_filter(namespace, filter)

            results = self.client.query_points(
                collection_name=collection,
                query=vector,
                query_filter=qdrant_filter,
                limit=top_k,
                with_payload=True,
            )

            matches = []
            for point in results.points:
                payload = dict(point.payload) if point.payload else {}
                payload.pop("namespace", None)
                matches.append(QueryMatch(score=point.score, metadata=payload))

            return matches
        except Exception as e:
            logger.error("Qdrant query error: %s", e)
            return []

    async def delete_by_document(self, document_id: str, namespace: str, index_name: Optional[str] = None) -> bool:
        """Delete all points for a given document_id in a namespace."""
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue

            collection = self._get_collection_name(index_name)
            self._ensure_collection(collection)

            self.client.delete(
                collection_name=collection,
                points_selector=Filter(
                    must=[
                        FieldCondition(key="namespace", match=MatchValue(value=namespace)),
                        FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                    ]
                ),
            )
            return True
        except Exception as e:
            logger.error("Qdrant delete error: %s", e)
            return False

    async def scroll_by_document(
        self,
        document_id: str,
        namespace: str,
        limit: int = 100,
        index_name: Optional[str] = None,
    ) -> List[Dict]:
        """Fetch all chunks for a document, returned sorted by chunk_index."""
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue

            collection = self._get_collection_name(index_name)
            self._ensure_collection(collection)

            scroll_filter = Filter(
                must=[
                    FieldCondition(key="namespace", match=MatchValue(value=namespace)),
                    FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                ]
            )

            points, _ = self.client.scroll(
                collection_name=collection,
                scroll_filter=scroll_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )

            chunks = []
            for point in points:
                payload = dict(point.payload) if point.payload else {}
                chunks.append({
                    "chunk_index": payload.get("chunk_index", 0),
                    "chunk_text": payload.get("chunk_text", ""),
                })
            chunks.sort(key=lambda x: x["chunk_index"])
            return chunks

        except Exception as e:
            logger.error("Qdrant scroll_by_document error: %s", e)
            return []

    async def copy_embeddings(
        self,
        source_doc_id: str,
        target_doc_id: str,
        source_namespace: str,
        target_namespace: str,
    ) -> bool:
        """Copy all embeddings from one document to another."""
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue, PointStruct

            collection = self._get_collection_name()
            self._ensure_collection(collection)

            scroll_filter = Filter(
                must=[
                    FieldCondition(key="namespace", match=MatchValue(value=source_namespace)),
                    FieldCondition(key="document_id", match=MatchValue(value=source_doc_id)),
                ]
            )

            # Scroll all matching points
            points, _ = self.client.scroll(
                collection_name=collection,
                scroll_filter=scroll_filter,
                limit=1000,
                with_payload=True,
                with_vectors=True,
            )

            if not points:
                return True

            new_points = []
            for point in points:
                new_payload = dict(point.payload) if point.payload else {}
                new_payload["document_id"] = target_doc_id
                new_payload["namespace"] = target_namespace

                # Generate new point ID based on target doc
                chunk_index = new_payload.get("chunk_index", 0)
                new_id = self._make_point_id(f"{target_doc_id}_{chunk_index}")

                new_points.append(PointStruct(
                    id=new_id,
                    vector=point.vector,
                    payload=new_payload,
                ))

            if new_points:
                self.client.upsert(collection_name=collection, points=new_points)

            return True
        except Exception as e:
            logger.error("Qdrant copy error: %s", e)
            return False


qdrant_client = QdrantVectorClient()
