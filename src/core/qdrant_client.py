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
    # "cosine" for dense search, "rrf" for fused hybrid results. RRF scores live
    # on a completely different scale (~0.016–0.033), so a cosine threshold
    # must never be applied to them.
    score_type: str = "cosine"


# Name of the sparse vector used for lexical (BM25-style) matching. The dense
# vector stays *unnamed* so collections written by earlier versions keep working.
SPARSE_VECTOR_NAME = "sparse"


class QdrantVectorClient:
    """Wrapper around qdrant-client for local file-based vector storage."""

    def __init__(self):
        self._client = None
        self._ensured_collections: set = set()
        self._hybrid_capable: Dict[str, bool] = {}

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
        """Create collection if it doesn't exist, and record hybrid capability."""
        if collection_name in self._ensured_collections:
            return
        try:
            from qdrant_client.models import (
                Distance, VectorParams, SparseVectorParams,
            )
            collections = [c.name for c in self.client.get_collections().collections]
            if collection_name not in collections:
                self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=settings.EMBEDDING_DIMENSIONS,
                        distance=Distance.COSINE,
                    ),
                    sparse_vectors_config={SPARSE_VECTOR_NAME: SparseVectorParams()},
                )
                self._hybrid_capable[collection_name] = True
                logger.info("Created Qdrant collection: %s (hybrid)", collection_name)
            else:
                # Collections created before hybrid support have no sparse config;
                # they keep working dense-only until reindexed.
                info = self.client.get_collection(collection_name)
                sparse_cfg = getattr(info.config.params, "sparse_vectors", None) or {}
                capable = SPARSE_VECTOR_NAME in sparse_cfg
                self._hybrid_capable[collection_name] = capable
                if not capable:
                    logger.warning(
                        "Collection '%s' has no sparse vector config — running "
                        "dense-only. Reindex to enable hybrid retrieval.",
                        collection_name,
                    )
            self._ensured_collections.add(collection_name)
        except Exception as e:
            logger.error("Failed to ensure collection %s: %s", collection_name, e)
            raise

    def supports_hybrid(self, index_name: Optional[str] = None) -> bool:
        collection = self._get_collection_name(index_name)
        self._ensure_collection(collection)
        return self._hybrid_capable.get(collection, False)

    @staticmethod
    def _make_point_id(string_id: str) -> str:
        """Convert a string ID to a deterministic UUID for Qdrant point IDs."""
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, string_id))

    def _build_filter(self, namespace: str, extra_filter: Optional[Dict] = None):
        """Build a Qdrant filter combining namespace + optional metadata filters.

        Supported value forms:
          ``{"$in": [...]}``          — field matches any of the values
          ``{"$in_or_null": [...]}``  — as above, or the field is unset/null
          plain value                 — exact match
        """
        from qdrant_client.models import (
            Filter, FieldCondition, MatchValue, MatchAny, IsNullCondition,
            IsEmptyCondition, PayloadField,
        )

        conditions = [
            FieldCondition(key="namespace", match=MatchValue(value=namespace))
        ]

        if extra_filter:
            for key, value in extra_filter.items():
                if isinstance(value, dict) and "$in" in value:
                    conditions.append(
                        FieldCondition(key=key, match=MatchAny(any=value["$in"]))
                    )
                elif isinstance(value, dict) and "$in_or_null" in value:
                    # Root-level documents carry no folder_id; they stay visible,
                    # matching the existing access policy. Points indexed before
                    # folder_id existed have the field missing, hence IsEmpty too.
                    allowed = value["$in_or_null"]
                    alternatives = [
                        IsNullCondition(is_null=PayloadField(key=key)),
                        IsEmptyCondition(is_empty=PayloadField(key=key)),
                    ]
                    if allowed:
                        alternatives.insert(
                            0, FieldCondition(key=key, match=MatchAny(any=allowed))
                        )
                    conditions.append(Filter(should=alternatives))
                else:
                    conditions.append(
                        FieldCondition(key=key, match=MatchValue(value=value))
                    )

        return Filter(must=conditions)

    async def upsert(self, vectors: List[Dict], namespace: str, index_name: Optional[str] = None) -> bool:
        """Store vectors in Qdrant.

        Each vector dict has 'id', 'values', 'metadata', and optionally
        'sparse' ({'indices': [...], 'values': [...]}) for hybrid retrieval.
        """
        try:
            from qdrant_client.models import PointStruct, SparseVector

            collection = self._get_collection_name(index_name)
            self._ensure_collection(collection)
            hybrid = self._hybrid_capable.get(collection, False)

            points = []
            for v in vectors:
                payload = dict(v.get("metadata", {}))
                payload["namespace"] = namespace

                sparse = v.get("sparse")
                if hybrid and sparse and sparse.get("indices"):
                    vector = {
                        "": v["values"],
                        SPARSE_VECTOR_NAME: SparseVector(
                            indices=sparse["indices"], values=sparse["values"]
                        ),
                    }
                else:
                    vector = v["values"]

                points.append(PointStruct(
                    id=self._make_point_id(v["id"]),
                    vector=vector,
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
        sparse_vector: Optional[Dict] = None,
    ) -> List[QueryMatch]:
        """Search vectors. Returns list of QueryMatch with .score and .metadata.

        When *sparse_vector* is supplied and the collection supports it, dense
        and lexical results are fused server-side with Reciprocal Rank Fusion;
        otherwise this is a plain dense search.
        """
        try:
            from qdrant_client.models import (
                Prefetch, SparseVector, FusionQuery, Fusion,
            )

            collection = self._get_collection_name(index_name)
            self._ensure_collection(collection)

            qdrant_filter = self._build_filter(namespace, filter)

            use_hybrid = (
                self._hybrid_capable.get(collection, False)
                and sparse_vector
                and sparse_vector.get("indices")
            )

            if use_hybrid:
                # Each branch fetches more than the final limit. With a branch
                # limit equal to top_k, a strong lexical-only hit can tie with a
                # weak dense-only hit at 1/(k+1) and lose the tie-break.
                prefetch_limit = max(top_k * 2, 20)
                results = self.client.query_points(
                    collection_name=collection,
                    prefetch=[
                        Prefetch(
                            query=vector,
                            using="",              # the unnamed dense vector
                            filter=qdrant_filter,
                            limit=prefetch_limit,
                        ),
                        Prefetch(
                            query=SparseVector(
                                indices=sparse_vector["indices"],
                                values=sparse_vector["values"],
                            ),
                            using=SPARSE_VECTOR_NAME,
                            filter=qdrant_filter,
                            limit=prefetch_limit,
                        ),
                    ],
                    query=FusionQuery(fusion=Fusion.RRF),
                    limit=top_k,
                    with_payload=True,
                )
            else:
                results = self.client.query_points(
                    collection_name=collection,
                    query=vector,
                    query_filter=qdrant_filter,
                    limit=top_k,
                    with_payload=True,
                )

            score_type = "rrf" if use_hybrid else "cosine"
            matches = []
            for point in results.points:
                payload = dict(point.payload) if point.payload else {}
                payload.pop("namespace", None)
                matches.append(
                    QueryMatch(score=point.score, metadata=payload, score_type=score_type)
                )

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

    async def fetch_chunks(
        self,
        document_id: str,
        namespace: str,
        chunk_indices: List[int],
        index_name: Optional[str] = None,
    ) -> List[Dict]:
        """Fetch specific chunks of a document by index (used to pull neighbours)."""
        if not chunk_indices:
            return []
        try:
            from qdrant_client.models import (
                Filter, FieldCondition, MatchValue, MatchAny,
            )

            collection = self._get_collection_name(index_name)
            self._ensure_collection(collection)

            points, _ = self.client.scroll(
                collection_name=collection,
                scroll_filter=Filter(must=[
                    FieldCondition(key="namespace", match=MatchValue(value=namespace)),
                    FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                    FieldCondition(key="chunk_index", match=MatchAny(any=list(chunk_indices))),
                ]),
                limit=len(chunk_indices),
                with_payload=True,
                with_vectors=False,
            )

            chunks = []
            for point in points:
                payload = dict(point.payload) if point.payload else {}
                payload.pop("namespace", None)
                chunks.append(payload)
            chunks.sort(key=lambda c: c.get("chunk_index", 0))
            return chunks
        except Exception as e:
            logger.error("Qdrant fetch_chunks error: %s", e)
            return []

    async def set_document_folder(
        self,
        document_id: str,
        namespace: str,
        folder_id: Optional[str],
        index_name: Optional[str] = None,
    ) -> bool:
        """Keep the payload's folder_id in step with a document move.

        Retrieval pre-filters on this field, so a stale value costs recall
        (never access — the database check downstream remains authoritative).
        """
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue

            collection = self._get_collection_name(index_name)
            self._ensure_collection(collection)

            self.client.set_payload(
                collection_name=collection,
                payload={"folder_id": str(folder_id) if folder_id else None},
                points=Filter(must=[
                    FieldCondition(key="namespace", match=MatchValue(value=namespace)),
                    FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                ]),
            )
            return True
        except Exception as e:
            logger.error("Qdrant set_document_folder error: %s", e)
            return False

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
