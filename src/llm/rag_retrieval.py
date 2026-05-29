"""Auto-split RAG service part."""
from typing import Optional, List, Dict, Any, AsyncGenerator
from uuid import UUID
import logging

from src.core.database import db
from src.core.openai_client import openai_client
from src.core.qdrant_client import qdrant_client
from src.access.permission import permission_service
from src.llm.web_search import web_search_service
from src.chat.service import chat_service
from src.llm.token_usage import token_usage_service
from src.config import settings

logger = logging.getLogger(__name__)


class RAGRetrievalMixin:
    @staticmethod
    async def get_accessible_documents_for_rag(
        user_id: UUID,
        org_id: Optional[UUID],
        document_ids: List[str]
    ) -> List[str]:
        """
        Filter document IDs to only those the user can access.

        For org users: Check folder-level permissions
        For personal users: All documents in their namespace are accessible
        """
        if not document_ids:
            return []

        if not org_id:
            # Personal user - all docs in their namespace are theirs
            return document_ids

        # Org user - check permissions
        is_admin = await permission_service.is_admin_or_owner(user_id, org_id)
        if is_admin:
            return document_ids  # Admin/Owner sees all

        # Get accessible folder IDs
        accessible_folder_ids = await permission_service.get_accessible_folder_ids(
            user_id, org_id
        )

        if not accessible_folder_ids:
            return []

        # Fetch documents and filter by parent_id
        docs_result = db.admin.table("storage_nodes").select(
            "id, parent_id"
        ).in_("id", document_ids).execute()

        accessible = []
        for doc in docs_result.data:
            parent_id = doc.get("parent_id")
            # Document is accessible if its parent folder is in accessible set
            # or if it's at root level (parent_id is None) - depends on your policy
            if parent_id and parent_id in accessible_folder_ids:
                accessible.append(doc["id"])
            elif not parent_id:
                # Root level - you may want to adjust this based on your policy
                # For now, allow root-level docs for org members
                accessible.append(doc["id"])

        return accessible

    @staticmethod
    async def search_documents(
        query: str,
        user_id: UUID,
        org_id: Optional[UUID],
        session_id: Optional[UUID] = None,
        top_k: int = None,
        search_main: bool = True,
        search_session: bool = True,
    ) -> List[dict]:
        """
        Search documents using vector similarity with permission filtering.

        Args:
            query: Search query
            user_id: User performing search
            org_id: Organization context (None for personal)
            session_id: Optional session to include session documents
            top_k: Number of results to return
            search_main: Whether to search the main (org/personal) index
            search_session: Whether to search the session-specific index
        """
        top_k = top_k or settings.RAG_TOP_K

        # Get query embedding
        query_embedding = await openai_client.get_embedding(query)

        if not query_embedding:
            logger.error("RAG: Failed to get embedding for query: %s", query)
            return []

        # Separate results from main index and chat-sessions index
        main_results = []
        session_results = []

        # Query main index (organization or personal documents)
        if search_main:
            namespace = str(org_id) if org_id else str(user_id)

            logger.debug("RAG: Searching main index in namespace: %s, top_k: %s", namespace, top_k)

            results = await qdrant_client.query(
                vector=query_embedding,
                namespace=namespace,
                top_k=top_k * 2,  # Get extra for filtering
            )
            if results:
                main_results = results
                logger.debug("RAG: Got %d results from main index", len(results))

        # Query chat-sessions index if we have a session AND searching session is enabled
        if session_id and search_session:
            logger.debug("RAG: Searching chat-sessions index for session: %s", session_id)
            
            # Determine namespace for session documents
            # Default to current user (uploader)
            session_namespace = str(user_id)
            
            try:
                # Fetch session to get the owner (documents are stored in owner's namespace)
                # Use db.admin to bypass RLS since we've already validated access in the API layer
                session_result = db.admin.table("chat_sessions").select("user_id").eq(
                    "id", str(session_id)
                ).single().execute()
                
                if session_result and session_result.data:
                    # Use Session Owner's ID as namespace
                    session_namespace = str(session_result.data["user_id"])
                    logger.debug("RAG: Session owner found: %s", session_namespace)
            except Exception as e:
                logger.error("RAG: Error fetching session owner: %s", e)

            logger.debug("RAG: Querying Pinecone chat-sessions index. Namespace: %s, SessionID: %s", session_namespace, session_id)
            session_index_results = await qdrant_client.query(
                vector=query_embedding,
                namespace=session_namespace,
                top_k=top_k * 2,
                filter={'session_id': str(session_id)},  # Filter by session for isolation
                index_name=settings.QDRANT_SESSIONS_COLLECTION
            )
            if session_index_results:
                session_results = session_index_results
                logger.debug("RAG: Got %d matches from Pinecone chat-sessions index", len(session_index_results))
            else:
                logger.debug("RAG: No matches from Pinecone chat-sessions index for session %s in namespace %s", session_id, session_namespace)

        if not main_results and not session_results:
            logger.debug("RAG: No results from Pinecone")
            return []

        # Process main index results with permission checks
        main_filtered_results = []
        if main_results:
            # Extract document IDs from main results
            doc_ids = [r.metadata.get('document_id') for r in main_results if r.metadata and r.metadata.get('document_id')]
            unique_doc_ids = list(set(doc_ids))

            # Filter by permissions
            accessible_doc_ids = await RAGService.get_accessible_documents_for_rag(
                user_id, org_id, unique_doc_ids
            )
            logger.debug("RAG: %d main index documents accessible after permission check", len(accessible_doc_ids))

            if accessible_doc_ids:
                # Get document details from storage_nodes
                docs_result = db.admin.table("storage_nodes").select(
                    "id, name"
                ).in_("id", accessible_doc_ids).eq("status", "active").execute()

                doc_map = {d['id']: d['name'] for d in docs_result.data if docs_result and docs_result.data}
                logger.debug("RAG: Found %d active main index documents in database", len(doc_map))

                # Build results from main index
                for r in main_results:
                    doc_id = r.metadata.get('document_id')
                    if doc_id in accessible_doc_ids and doc_id in doc_map:
                        main_filtered_results.append({
                            'document_id': doc_id,
                            'document_name': doc_map.get(doc_id, 'Unknown'),
                            'chunk_text': r.metadata.get('chunk_text', ''),
                            'chunk_index': r.metadata.get('chunk_index', 0),
                            'section_header': r.metadata.get('section_header', ''),
                            'page_numbers': r.metadata.get('page_numbers', []),
                            'chunk_type': r.metadata.get('chunk_type', 'text'),
                            'score': r.score
                        })

        # Process session index results - no permission check needed (session isolation is enough)
        session_filtered_results = []
        if session_results:
            # Get unique session document IDs from session results
            session_doc_ids = list(set([r.metadata.get('document_id') for r in session_results if r.metadata and r.metadata.get('document_id')]))

            # Get document names from session_documents table (only completed embeddings)
            session_docs_result = db.admin.table("session_documents").select(
                "id, filename"
            ).in_("id", session_doc_ids).eq(
                "session_id", str(session_id)
            ).eq(
                "embedding_status", "completed"  # Only use documents with completed embeddings
            ).execute()

            session_doc_map = {d['id']: d['filename'] for d in session_docs_result.data if session_docs_result and session_docs_result.data}
            logger.debug("RAG: Found %d completed session documents (out of %d total)", len(session_doc_map), len(session_doc_ids))

            # Build results from session index
            for r in session_results:
                session_doc_id = r.metadata.get('document_id')
                if session_doc_id in session_doc_map:
                    # Boost score for session documents to prioritize them
                    # This ensures session-specific context is not "crowded out" by main index results
                    boosted_score = r.score * 1.1

                    session_filtered_results.append({
                        'document_id': session_doc_id,
                        'document_name': session_doc_map.get(session_doc_id, 'Unknown'),
                        'chunk_text': r.metadata.get('chunk_text', ''),
                        'chunk_index': r.metadata.get('chunk_index', 0),
                        'section_header': r.metadata.get('section_header', ''),
                        'page_numbers': r.metadata.get('page_numbers', []),
                        'chunk_type': r.metadata.get('chunk_type', 'text'),
                        'score': boosted_score,
                        'source': 'session',
                    })

        # Combine and sort by score
        all_filtered_results = main_filtered_results + session_filtered_results
        all_filtered_results.sort(key=lambda x: x['score'], reverse=True)

        # Take top K
        final_results = all_filtered_results[:top_k]

        logger.debug("RAG: Returning %d chunks (%d from session, %d from main index)", len(final_results), len([r for r in final_results if r.get('source') == 'session']), len([r for r in final_results if not r.get('source')]))
        if final_results:
            logger.debug("RAG: Score range: %.3f to %.3f", final_results[0]['score'], final_results[-1]['score'])

        return final_results
