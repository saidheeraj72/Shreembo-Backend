"""Pinecone client for vector embeddings."""
from typing import List, Dict, Optional
from src.config import settings


class PineconeClient:
    def __init__(self):
        self._client = None
        self._index = None

    @property
    def client(self):
        if self._client is None:
            from pinecone import Pinecone
            self._client = Pinecone(api_key=settings.PINECONE_API_KEY)
        return self._client

    @property
    def index(self):
        if self._index is None:
            self._index = self.client.Index(settings.PINECONE_INDEX_NAME)
        return self._index

    async def upsert(self, vectors: List[Dict], namespace: str) -> bool:
        try:
            self.index.upsert(vectors=vectors, namespace=namespace)
            return True
        except Exception as e:
            print(f"Pinecone upsert error: {e}")
            return False

    async def query(self, vector: List[float], namespace: str, top_k: int = 10,
                    filter: Optional[Dict] = None) -> List[Dict]:
        try:
            results = self.index.query(
                vector=vector, namespace=namespace, top_k=top_k,
                filter=filter, include_metadata=True
            )
            return results.matches
        except Exception as e:
            print(f"Pinecone query error: {e}")
            return []

    async def delete_by_document(self, document_id: str, namespace: str) -> bool:
        try:
            self.index.delete(filter={'document_id': document_id}, namespace=namespace)
            return True
        except Exception:
            return False

    async def copy_embeddings(self, source_doc_id: str, target_doc_id: str,
                               source_namespace: str, target_namespace: str) -> bool:
        """Copy all embeddings from one document to another (for replication)."""
        try:
            # Query all vectors for the source document
            # First get a sample to find vector IDs
            results = self.index.query(
                vector=[0.0] * settings.EMBEDDING_DIMENSIONS,
                namespace=source_namespace,
                top_k=1000,
                filter={'document_id': source_doc_id},
                include_metadata=True,
                include_values=True
            )

            if not results.matches:
                return True  # No embeddings to copy

            # Prepare new vectors with updated metadata
            new_vectors = []
            for match in results.matches:
                new_id = f"{target_doc_id}_{match.id.split('_')[-1]}"
                new_metadata = dict(match.metadata)
                new_metadata['document_id'] = target_doc_id
                new_vectors.append({
                    'id': new_id,
                    'values': match.values,
                    'metadata': new_metadata
                })

            # Upsert to target namespace
            if new_vectors:
                self.index.upsert(vectors=new_vectors, namespace=target_namespace)

            return True
        except Exception as e:
            print(f"Pinecone copy error: {e}")
            return False


pinecone_client = PineconeClient()
