"""Embedding extraction and chunking helpers."""
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import tiktoken

from src.core.s3 import s3_client
from src.utils.text_utils import sanitize_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token counter — sized for text-embedding-3-small (cl100k_base)
# ---------------------------------------------------------------------------

_enc = tiktoken.get_encoding("cl100k_base")


def _token_len(text: str) -> int:
    return len(_enc.encode(text, disallowed_special=()))


# ---------------------------------------------------------------------------
# Chunk data structure
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """A text chunk with contextual metadata for high-quality RAG retrieval."""
    text: str
    chunk_index: int = 0
    page_numbers: List[int] = field(default_factory=list)
    section_header: str = ""        # e.g. "Section 3 > Subsection A"
    chunk_type: str = "text"        # text | table | code | list


# ---------------------------------------------------------------------------
# Extraction via `unstructured`
# ---------------------------------------------------------------------------

def _extract_with_unstructured(file_bytes: bytes, file_type: str) -> Optional[str]:
    """Extract structured text from any document using the `unstructured` library.

    Returns markdown-formatted text with page annotations and table preservation.
    """
    try:
        from unstructured.partition.auto import partition

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            elements = partition(
                filename=tmp_path,
                strategy="auto",          # fast for text PDFs, OCR for scanned
                include_page_breaks=True,
            )
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        if not elements:
            return None

        parts: List[str] = []
        current_page = 0

        for el in elements:
            meta = el.metadata
            page = getattr(meta, "page_number", None)

            # Insert page separator when page changes
            if page and page != current_page:
                if current_page > 0:
                    parts.append("\n\n---\n\n")
                current_page = page

            category = el.category  # Title, NarrativeText, Table, ListItem, etc.
            text = str(el).strip()
            if not text:
                continue

            if category == "Title":
                # Estimate heading level from font size / nesting (default ##)
                depth = getattr(meta, "category_depth", None) or 1
                prefix = "#" * min(depth + 1, 6)
                parts.append(f"\n{prefix} {text}\n")
            elif category == "Table":
                # unstructured returns tables as HTML; keep as-is for the chunker
                html_table = getattr(el.metadata, "text_as_html", None)
                if html_table:
                    parts.append(f"\n{html_table}\n")
                else:
                    parts.append(f"\n{text}\n")
            elif category == "ListItem":
                parts.append(f"- {text}")
            elif category == "Header":
                parts.append(f"\n## {text}\n")
            elif category == "Footer" or category == "PageNumber":
                continue  # skip noise
            else:
                parts.append(text)

        result = "\n".join(parts).strip()
        return result if result else None

    except Exception as e:
        logger.warning("unstructured extraction failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Fallback extractors
# ---------------------------------------------------------------------------

def _extract_pdf_with_pymupdf(file_bytes: bytes) -> Optional[str]:
    """Fallback PDF extractor using pymupdf4llm."""
    try:
        import pymupdf
        import pymupdf4llm

        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        md_text = pymupdf4llm.to_markdown(doc, page_chunks=False)
        doc.close()
        return md_text if md_text and md_text.strip() else None
    except Exception as e:
        logger.warning("PyMuPDF fallback failed: %s", e)
        return None


def _extract_pdf_with_ocr(file_bytes: bytes) -> Optional[str]:
    """Last-resort: plain text extraction + OCR for scanned PDFs."""
    try:
        import pymupdf

        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        pages_text = []
        for page_num, page in enumerate(doc, 1):
            text = page.get_text("text")
            if text and text.strip():
                pages_text.append(f"<!-- page {page_num} -->\n{text.strip()}")
            else:
                try:
                    tp = page.get_textpage_ocr(language="eng", dpi=300)
                    ocr_text = page.get_text("text", textpage=tp)
                    if ocr_text and ocr_text.strip():
                        pages_text.append(f"<!-- page {page_num} -->\n{ocr_text.strip()}")
                except Exception:
                    pass
        doc.close()
        full_text = "\n\n---\n\n".join(pages_text)
        return full_text if full_text.strip() else None
    except Exception as e:
        logger.warning("OCR extraction failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Page-number extraction from markdown page separators
# ---------------------------------------------------------------------------

def _annotate_pages(text: str) -> List[Dict]:
    """Split markdown text into per-page blocks with page numbers."""
    blocks = re.split(r"\n-{3,}\n", text)
    result = []
    for i, block in enumerate(blocks):
        stripped = block.strip()
        if stripped:
            result.append({"page": i + 1, "text": stripped})
    return result if result else [{"page": 1, "text": text}]


# ---------------------------------------------------------------------------
# Heading hierarchy tracker
# ---------------------------------------------------------------------------

class _HeadingTracker:
    """Maintains a stack of markdown headings to build section breadcrumbs."""

    def __init__(self):
        self._stack: List[tuple] = []

    def update(self, heading_line: str) -> str:
        match = re.match(r"^(#{1,6})\s+(.+)$", heading_line.strip())
        if not match:
            return self.current
        level = len(match.group(1))
        title = match.group(2).strip()
        self._stack = [(lv, t) for lv, t in self._stack if lv < level]
        self._stack.append((level, title))
        return self.current

    @property
    def current(self) -> str:
        if not self._stack:
            return ""
        return " > ".join(t for _, t in self._stack)


# ---------------------------------------------------------------------------
# Table / HTML table detection
# ---------------------------------------------------------------------------

_TABLE_ROW_RE = re.compile(r"^\|.*\|$")
_TABLE_SEP_RE = re.compile(r"^\|[\s:_-]+\|$")
_HTML_TABLE_RE = re.compile(r"<table[\s>]", re.IGNORECASE)


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return bool(_TABLE_ROW_RE.match(stripped) or _TABLE_SEP_RE.match(stripped))


def _is_html_table_block(text: str) -> bool:
    return bool(_HTML_TABLE_RE.search(text))


# ---------------------------------------------------------------------------
# Smart chunker — token-aware
# ---------------------------------------------------------------------------

# Defaults tuned for text-embedding-3-small (8191 token limit)
MAX_CHUNK_TOKENS = 512    # ~2000 chars — sweet spot for retrieval quality
OVERLAP_TOKENS = 50       # ~200 chars
MIN_CHUNK_TOKENS = 30     # ~100 chars


class SmartDocumentChunker:
    """Production-grade markdown chunker with:

    - Token-aware sizing (tiktoken cl100k_base)
    - Heading-aware splitting with breadcrumb context
    - Table preservation (never splits inside a table)
    - HTML table support (from unstructured)
    - Chunk overlap for boundary context
    - Page number tracking
    """

    def __init__(
        self,
        max_chunk_tokens: int = MAX_CHUNK_TOKENS,
        overlap_tokens: int = OVERLAP_TOKENS,
        min_chunk_tokens: int = MIN_CHUNK_TOKENS,
    ):
        self.max_tokens = max_chunk_tokens
        self.overlap_tokens = overlap_tokens
        self.min_tokens = min_chunk_tokens

    def chunk_document(self, text: str) -> List[Chunk]:
        """Main entry: split full document text into Chunk objects."""
        page_blocks = _annotate_pages(text)
        raw_segments = self._segment_by_headings_and_tables(page_blocks)
        chunks = self._merge_segments(raw_segments)
        chunks = self._add_overlap(chunks)

        final = []
        for c in chunks:
            if _token_len(c.text) < self.min_tokens:
                continue
            final.append(c)

        for i, c in enumerate(final):
            c.chunk_index = i

        return final

    # ----- Phase 1: segment into atomic blocks ---------------------------

    def _segment_by_headings_and_tables(
        self, page_blocks: List[Dict]
    ) -> List[Dict]:
        tracker = _HeadingTracker()
        segments: List[Dict] = []

        for pb in page_blocks:
            page_num = pb["page"]
            text = pb["text"]

            # Handle HTML tables from unstructured: split them out first
            if _is_html_table_block(text):
                self._split_html_tables(text, page_num, tracker, segments)
                continue

            lines = text.split("\n")
            current_lines: List[str] = []
            current_type = "text"
            in_table = False

            for line in lines:
                is_heading = line.strip().startswith("#") and re.match(
                    r"^#{1,6}\s+", line.strip()
                )
                is_table = _is_table_line(line)

                if is_heading:
                    if current_lines:
                        segments.append({
                            "text": "\n".join(current_lines).strip(),
                            "pages": [page_num],
                            "section_header": tracker.current,
                            "chunk_type": current_type,
                        })
                        current_lines = []
                        current_type = "text"
                        in_table = False

                    tracker.update(line)
                    current_lines.append(line)
                    continue

                if is_table and not in_table:
                    if current_lines and current_type != "table":
                        segments.append({
                            "text": "\n".join(current_lines).strip(),
                            "pages": [page_num],
                            "section_header": tracker.current,
                            "chunk_type": current_type,
                        })
                        current_lines = []
                    in_table = True
                    current_type = "table"
                    current_lines.append(line)
                    continue

                if in_table:
                    if is_table or line.strip() == "":
                        current_lines.append(line)
                        continue
                    else:
                        segments.append({
                            "text": "\n".join(current_lines).strip(),
                            "pages": [page_num],
                            "section_header": tracker.current,
                            "chunk_type": "table",
                        })
                        current_lines = [line]
                        current_type = "text"
                        in_table = False
                        continue

                current_lines.append(line)

            if current_lines:
                segments.append({
                    "text": "\n".join(current_lines).strip(),
                    "pages": [page_num],
                    "section_header": tracker.current,
                    "chunk_type": current_type,
                })

        return [s for s in segments if s["text"].strip()]

    def _split_html_tables(
        self, text: str, page_num: int, tracker: _HeadingTracker, segments: List[Dict]
    ):
        """Split text containing HTML tables into table and non-table segments."""
        # Split around <table>...</table> blocks
        parts = re.split(r"(<table[\s\S]*?</table>)", text, flags=re.IGNORECASE)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if re.match(r"<table", part, re.IGNORECASE):
                segments.append({
                    "text": part,
                    "pages": [page_num],
                    "section_header": tracker.current,
                    "chunk_type": "table",
                })
            else:
                # Process non-table text for headings
                for line in part.split("\n"):
                    if line.strip().startswith("#") and re.match(r"^#{1,6}\s+", line.strip()):
                        tracker.update(line)
                segments.append({
                    "text": part,
                    "pages": [page_num],
                    "section_header": tracker.current,
                    "chunk_type": "text",
                })

    # ----- Phase 2: merge small segments, split large ones ---------------

    def _merge_segments(self, segments: List[Dict]) -> List[Chunk]:
        chunks: List[Chunk] = []
        current_text = ""
        current_pages: List[int] = []
        current_header = ""
        current_type = "text"

        for seg in segments:
            seg_text = seg["text"]
            seg_type = seg["chunk_type"]
            seg_pages = seg["pages"]
            seg_header = seg["section_header"]

            if seg_type == "table":
                if current_text.strip():
                    chunks.extend(
                        self._make_chunks(current_text, current_pages, current_header, current_type)
                    )
                    current_text = ""
                    current_pages = []
                chunks.extend(
                    self._make_chunks(seg_text, seg_pages, seg_header, "table")
                )
                current_header = seg_header
                current_type = "text"
                continue

            if seg_type != current_type and current_text.strip():
                chunks.extend(
                    self._make_chunks(current_text, current_pages, current_header, current_type)
                )
                current_text = ""
                current_pages = []

            combined = f"{current_text}\n\n{seg_text}" if current_text else seg_text
            if _token_len(combined) <= self.max_tokens:
                current_text = combined
                current_pages = list(set(current_pages + seg_pages))
                current_header = seg_header or current_header
                current_type = seg_type
            else:
                if current_text.strip():
                    chunks.extend(
                        self._make_chunks(current_text, current_pages, current_header, current_type)
                    )
                current_text = seg_text
                current_pages = seg_pages[:]
                current_header = seg_header
                current_type = seg_type

        if current_text.strip():
            chunks.extend(
                self._make_chunks(current_text, current_pages, current_header, current_type)
            )

        return chunks

    def _make_chunks(
        self, text: str, pages: List[int], header: str, chunk_type: str
    ) -> List[Chunk]:
        if _token_len(text) <= self.max_tokens:
            return [Chunk(
                text=text.strip(),
                page_numbers=sorted(set(pages)),
                section_header=header,
                chunk_type=chunk_type,
            )]

        paragraphs = text.split("\n\n")
        chunks: List[Chunk] = []
        current = ""

        for para in paragraphs:
            candidate = f"{current}\n\n{para}" if current else para
            if _token_len(candidate) <= self.max_tokens:
                current = candidate
            else:
                if current.strip():
                    chunks.append(Chunk(
                        text=current.strip(),
                        page_numbers=sorted(set(pages)),
                        section_header=header,
                        chunk_type=chunk_type,
                    ))
                if _token_len(para) > self.max_tokens:
                    chunks.extend(self._force_split(para, pages, header, chunk_type))
                    current = ""
                else:
                    current = para

        if current.strip():
            chunks.append(Chunk(
                text=current.strip(),
                page_numbers=sorted(set(pages)),
                section_header=header,
                chunk_type=chunk_type,
            ))

        return chunks

    def _force_split(
        self, text: str, pages: List[int], header: str, chunk_type: str
    ) -> List[Chunk]:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks: List[Chunk] = []
        current = ""
        for sent in sentences:
            candidate = f"{current} {sent}" if current else sent
            if _token_len(candidate) <= self.max_tokens:
                current = candidate
            else:
                if current.strip():
                    chunks.append(Chunk(
                        text=current.strip(),
                        page_numbers=sorted(set(pages)),
                        section_header=header,
                        chunk_type=chunk_type,
                    ))
                current = sent
        if current.strip():
            chunks.append(Chunk(
                text=current.strip(),
                page_numbers=sorted(set(pages)),
                section_header=header,
                chunk_type=chunk_type,
            ))
        return chunks

    # ----- Phase 3: add overlap ------------------------------------------

    def _add_overlap(self, chunks: List[Chunk]) -> List[Chunk]:
        if len(chunks) <= 1 or self.overlap_tokens <= 0:
            return chunks

        for i in range(1, len(chunks)):
            prev_text = chunks[i - 1].text
            prev_tokens = _enc.encode(prev_text, disallowed_special=())
            if len(prev_tokens) <= self.overlap_tokens:
                overlap = prev_text
            else:
                overlap = _enc.decode(prev_tokens[-self.overlap_tokens:])
                # Clean up to start at a word boundary
                space_idx = overlap.find(" ")
                if space_idx != -1:
                    overlap = overlap[space_idx + 1:]

            chunks[i].text = f"[...] {overlap}\n\n{chunks[i].text}"

        return chunks


# ---------------------------------------------------------------------------
# EmbeddingCoreMixin — extraction + public API
# ---------------------------------------------------------------------------

class EmbeddingCoreMixin:
    """Core extraction/chunking methods used by EmbeddingService."""

    @staticmethod
    async def extract_text(s3_key: str, file_type: str) -> Optional[str]:
        content = await s3_client.get_file_content(s3_key)
        if not content:
            return None

        text = None

        # 1. Try `unstructured` first — best quality for all file types
        text = _extract_with_unstructured(content, file_type)

        if text and text.strip():
            logger.info("Extracted with unstructured (%d chars) for %s", len(text), s3_key)
            return sanitize_text(text)

        # 2. PDF fallback chain: pymupdf4llm → OCR
        if file_type == "pdf":
            logger.info("unstructured returned empty, trying pymupdf4llm for %s", s3_key)
            text = _extract_pdf_with_pymupdf(content)
            if not text:
                logger.info("pymupdf4llm empty, trying OCR for %s", s3_key)
                text = _extract_pdf_with_ocr(content)

        return sanitize_text(text) if text else None

    @staticmethod
    def chunk_text(text: str) -> List[Chunk]:
        """Chunk document text into enriched Chunk objects (token-aware)."""
        chunker = SmartDocumentChunker(
            max_chunk_tokens=MAX_CHUNK_TOKENS,
            overlap_tokens=OVERLAP_TOKENS,
            min_chunk_tokens=MIN_CHUNK_TOKENS,
        )
        return chunker.chunk_document(text)
