"""
Reranker for RAG chunks.

Primary:  FlashRank neural cross-encoder (ms-marco-MiniLM-L-12-v2)
          Install with: pip install flashrank
Fallback: BM25 keyword scoring + vector cosine Reciprocal Rank Fusion (pure Python)
"""
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

try:
    from flashrank import Ranker, RerankRequest as _FlashRerankRequest
    _ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2", cache_dir="/tmp/flashrank_cache")
    RERANKER_AVAILABLE = True
    logger.info("FlashRank reranker loaded (ms-marco-MiniLM-L-12-v2)")
except Exception:
    RERANKER_AVAILABLE = False
    logger.info("FlashRank not installed — using BM25+vector RRF reranking")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bm25_score(query_terms: List[str], doc_terms: List[str], avg_doc_len: float) -> float:
    """Approximate BM25 score for a single document."""
    k1, b = 1.5, 0.75
    doc_len = len(doc_terms)
    score = 0.0
    for term in query_terms:
        tf = doc_terms.count(term)
        if tf == 0:
            continue
        score += (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / max(avg_doc_len, 1)))
    return score


def _deduplicate(chunks: List[Dict]) -> List[Dict]:
    """Drop exact duplicates (same document_id + chunk_index)."""
    seen: set = set()
    unique: List[Dict] = []
    for c in chunks:
        key = (c.get("document_id", ""), c.get("chunk_index", 0))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rerank(query: str, chunks: List[Dict], top_k: int, min_score: float = 0.0) -> List[Dict]:
    """
    Rerank and filter RAG chunks by relevance to *query*.

    Steps:
      1. Minimum cosine similarity threshold filter
      2. Deduplication (same doc + chunk_index)
      3. Neural reranking (FlashRank) OR BM25+vector RRF fusion
      4. Return top_k results
    """
    if not chunks:
        return []

    # 1. Score threshold
    if min_score > 0:
        chunks = [c for c in chunks if c.get("score", 0) >= min_score]

    # 2. Deduplicate
    chunks = _deduplicate(chunks)

    if not chunks:
        return []

    take = min(top_k, len(chunks))

    # 3a. FlashRank neural reranker
    if RERANKER_AVAILABLE:
        try:
            passages = [{"id": i, "text": c.get("chunk_text", "")} for i, c in enumerate(chunks)]
            request = _FlashRerankRequest(query=query, passages=passages)
            results = _ranker.rerank(request)
            reranked = [chunks[r["id"]] for r in results[:take]]
            logger.debug("FlashRank: %d → %d chunks", len(chunks), len(reranked))
            return reranked
        except Exception as e:
            logger.error("FlashRank failed, falling back to BM25+RRF: %s", e)

    # 3b. BM25 + vector cosine Reciprocal Rank Fusion
    query_terms = query.lower().split()
    all_doc_terms = [c.get("chunk_text", "").lower().split() for c in chunks]
    avg_doc_len = sum(len(t) for t in all_doc_terms) / len(all_doc_terms)

    vector_order = sorted(range(len(chunks)), key=lambda i: chunks[i].get("score", 0), reverse=True)
    bm25_scores = [_bm25_score(query_terms, dt, avg_doc_len) for dt in all_doc_terms]
    bm25_order = sorted(range(len(chunks)), key=lambda i: bm25_scores[i], reverse=True)

    k = 60  # standard RRF constant
    rrf: Dict[int, float] = {}
    for rank, idx in enumerate(vector_order):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (k + rank + 1)
    for rank, idx in enumerate(bm25_order):
        rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (k + rank + 1)

    reranked_indices = sorted(rrf.keys(), key=lambda i: rrf[i], reverse=True)
    result = [chunks[i] for i in reranked_indices[:take]]
    logger.debug("BM25+RRF: %d → %d chunks", len(chunks), len(result))
    return result
