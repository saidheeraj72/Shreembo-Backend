"""BM25-style sparse vectors for hybrid retrieval.

Dense embeddings match on meaning but routinely miss exact tokens — invoice
numbers, SKUs, error codes, surnames. A sparse lexical vector alongside the
dense one catches those; Qdrant fuses the two rankings server-side with RRF.

This is a self-contained encoder (no extra model dependency). Term weights use
BM25 term-frequency saturation with length normalisation against a fixed
average document length, which is a good approximation when every document is
a similarly-sized chunk.
"""
import hashlib
import re
from dataclasses import dataclass
from typing import List, Tuple

# Tokens keep internal separators so identifiers survive as a unit
# ("INV-2291", "acme_corp", "v1.2"), and their parts are indexed too.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_/.][a-z0-9]+)*")
_SPLIT_RE = re.compile(r"[-_/.]")

_STOPWORDS = frozenset("""
a an and are as at be been but by for from has have he her his how i if in into is it
its of on or she that the their them then there these they this to was were what when
where which who will with would you your our us we
""".split())

# BM25 parameters — b/k1 are the standard defaults; avgdl approximates the
# average chunk length in tokens (chunks are capped at 512).
_K1 = 1.5
_B = 0.75
_AVG_DOC_LEN = 350

# Sparse indices must be stable across processes, so hash with blake2b rather
# than Python's salted hash().
_INDEX_SPACE = 2 ** 31 - 1


@dataclass
class SparseVec:
    indices: List[int]
    values: List[float]

    def __bool__(self) -> bool:
        return bool(self.indices)

    def as_dict(self) -> dict:
        return {"indices": self.indices, "values": self.values}


def _term_index(term: str) -> int:
    digest = hashlib.blake2b(term.encode("utf-8"), digest_size=6).digest()
    return int.from_bytes(digest, "big") % _INDEX_SPACE


def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        token = match.group(0)
        if len(token) < 2 or token in _STOPWORDS:
            continue
        tokens.append(token)
        # Index the parts of a compound identifier too, so "2291" finds "INV-2291"
        if _SPLIT_RE.search(token):
            for part in _SPLIT_RE.split(token):
                if len(part) >= 2 and part not in _STOPWORDS:
                    tokens.append(part)
    return tokens


def _collapse(pairs: List[Tuple[int, float]]) -> SparseVec:
    """Sum duplicate indices and emit them in ascending order."""
    merged: dict = {}
    for index, value in pairs:
        merged[index] = merged.get(index, 0.0) + value
    ordered = sorted(merged.items())
    return SparseVec(
        indices=[i for i, _ in ordered],
        values=[round(v, 6) for _, v in ordered],
    )


def encode_document(text: str) -> SparseVec:
    """Encode stored text with BM25 term-frequency weights."""
    tokens = _tokenize(text)
    if not tokens:
        return SparseVec([], [])

    counts: dict = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1

    doc_len = len(tokens)
    norm = _K1 * (1 - _B + _B * doc_len / _AVG_DOC_LEN)

    return _collapse([
        (_term_index(term), (tf * (_K1 + 1)) / (tf + norm))
        for term, tf in counts.items()
    ])


def encode_query(text: str) -> SparseVec:
    """Encode a query — presence-weighted, so no single repeated term dominates."""
    tokens = _tokenize(text)
    if not tokens:
        return SparseVec([], [])
    return _collapse([(_term_index(term), 1.0) for term in set(tokens)])
