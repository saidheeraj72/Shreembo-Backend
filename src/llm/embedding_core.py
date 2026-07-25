"""Embedding extraction and chunking helpers."""
import asyncio
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
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
    text: str                       # clean text — stored and shown as the source
    chunk_index: int = 0
    page_numbers: List[int] = field(default_factory=list)
    section_header: str = ""        # e.g. "Section 3 > Subsection A"
    chunk_type: str = "text"        # text | table | code | list
    embed_text: str = ""            # text sent to the embedding model (adds overlap)

    @property
    def text_to_embed(self) -> str:
        return self.embed_text or self.text


# ---------------------------------------------------------------------------
# Page markers
#
# Extractors emit `<!-- page N -->` before each page's content so the chunker
# can attach true page numbers. Formats without pages (docx, md, csv, html…)
# emit no markers and their chunks carry no page numbers at all.
# ---------------------------------------------------------------------------

_PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")


def _page_marker(page: int) -> str:
    return f"<!-- page {page} -->"


# ---------------------------------------------------------------------------
# HTML table → markdown
#
# `unstructured` returns tables as HTML. Embedding tag soup wastes tokens and
# dilutes the vector, so tables are normalised to markdown, which the chunker
# can also split row-wise while repeating the header.
# ---------------------------------------------------------------------------

class _TableHTMLParser(HTMLParser):
    """Collect <tr>/<td>/<th> cell text from an HTML table."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            cell = " ".join("".join(self._cell).split()).replace("|", r"\|")
            if self._row is None:
                self._row = []
            self._row.append(cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _html_table_to_markdown(html: str) -> Optional[str]:
    """Convert an HTML table to a markdown table. Returns None if unparseable."""
    try:
        parser = _TableHTMLParser()
        parser.feed(html)
        parser.close()
    except Exception as e:
        logger.debug("HTML table parse failed: %s", e)
        return None

    rows = [r for r in parser.rows if any(c.strip() for c in r)]
    if not rows:
        return None

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Extraction via `markitdown` (non-PDF formats)
# ---------------------------------------------------------------------------

def _extract_with_markitdown(file_bytes: bytes, file_type: str) -> Optional[str]:
    """Extract text from non-PDF documents using MarkItDown."""
    try:
        from markitdown import MarkItDown

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            md = MarkItDown()
            result = md.convert(tmp_path)
            text = result.text_content
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        return text if text and text.strip() else None

    except Exception as e:
        logger.warning("MarkItDown extraction failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Extraction via `unstructured` (PDFs)
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

            # Mark the true page number whenever the page changes
            if page and page != current_page:
                current_page = page
                parts.append(f"\n{_page_marker(page)}\n")

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
                # unstructured returns tables as HTML — normalise to markdown so
                # the chunker can split them row-wise and keep the header
                html_table = getattr(el.metadata, "text_as_html", None)
                md_table = _html_table_to_markdown(html_table) if html_table else None
                parts.append(f"\n{md_table or text}\n")
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
        pages = pymupdf4llm.to_markdown(doc, page_chunks=True)
        doc.close()

        parts: List[str] = []
        for i, page in enumerate(pages, 1):
            page_text = (page.get("text") or "").strip()
            if not page_text:
                continue
            page_no = (page.get("metadata") or {}).get("page") or i
            parts.append(f"{_page_marker(page_no)}\n{page_text}")

        md_text = "\n\n".join(parts)
        return md_text if md_text.strip() else None
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
                pages_text.append(f"{_page_marker(page_num)}\n{text.strip()}")
            else:
                try:
                    tp = page.get_textpage_ocr(language="eng", dpi=300)
                    ocr_text = page.get_text("text", textpage=tp)
                    if ocr_text and ocr_text.strip():
                        pages_text.append(f"{_page_marker(page_num)}\n{ocr_text.strip()}")
                except Exception:
                    pass
        doc.close()
        full_text = "\n\n".join(pages_text)
        return full_text if full_text.strip() else None
    except Exception as e:
        logger.warning("OCR extraction failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Page-number extraction from `<!-- page N -->` markers
# ---------------------------------------------------------------------------

def _annotate_pages(text: str) -> List[Dict]:
    """Split text into blocks carrying their true page number.

    Blocks from documents without page markers (docx, md, csv, html…) get
    ``page: None`` so no page number is ever attributed to them.
    """
    parts = _PAGE_MARKER_RE.split(text)

    # No markers at all — one unpaginated block
    if len(parts) == 1:
        stripped = text.strip()
        return [{"page": None, "text": stripped}] if stripped else []

    blocks: List[Dict] = []

    # Anything before the first marker has no known page
    preamble = parts[0].strip()
    if preamble:
        blocks.append({"page": None, "text": preamble})

    # parts is [preamble, page_no, body, page_no, body, ...]
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1].strip()
        if not body:
            continue
        try:
            page = int(parts[i])
        except (TypeError, ValueError):
            page = None
        blocks.append({"page": page, "text": body})

    return blocks


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
# Full markdown alignment row, e.g. "| --- | :---: |" — the loose _TABLE_SEP_RE
# above only matches single-column separators.
_TABLE_SEP_LINE_RE = re.compile(r"^\|(?:\s*:?-{2,}:?\s*\|)+$")
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
MAX_CHUNK_TOKENS = 512          # ~2000 chars — sweet spot for retrieval quality
TABLE_MAX_CHUNK_TOKENS = 900    # tables stay coherent with more rows per chunk
OVERLAP_TOKENS = 50             # ~200 chars
MIN_CHUNK_TOKENS = 20           # ~80 chars


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
        table_max_chunk_tokens: int = TABLE_MAX_CHUNK_TOKENS,
    ):
        self.max_tokens = max_chunk_tokens
        self.overlap_tokens = overlap_tokens
        self.min_tokens = min_chunk_tokens
        self.table_max_tokens = max(table_max_chunk_tokens, max_chunk_tokens)

    def _limit_for(self, chunk_type: str) -> int:
        return self.table_max_tokens if chunk_type == "table" else self.max_tokens

    def chunk_document(self, text: str) -> List[Chunk]:
        """Main entry: split full document text into Chunk objects."""
        page_blocks = _annotate_pages(text)
        raw_segments = self._segment_by_headings_and_tables(page_blocks)
        chunks = self._merge_segments(raw_segments)

        # Drop undersized chunks *before* overlap, so borderline junk can't be
        # rescued by text borrowed from its neighbour. Tables are exempt: a
        # two-row table is short but is often the whole answer.
        final = [
            c for c in chunks
            if c.chunk_type == "table" or _token_len(c.text) >= self.min_tokens
        ]

        for i, c in enumerate(final):
            c.chunk_index = i

        self._add_overlap(final)

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
            # Unpaginated formats carry no page number at all
            seg_pages: List[int] = [page_num] if page_num else []

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
                            "pages": list(seg_pages),
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
                            "pages": list(seg_pages),
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
                            "pages": list(seg_pages),
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
                    "pages": list(seg_pages),
                    "section_header": tracker.current,
                    "chunk_type": current_type,
                })

        return [s for s in segments if s["text"].strip()]

    def _split_html_tables(
        self, text: str, page_num: Optional[int], tracker: _HeadingTracker,
        segments: List[Dict]
    ):
        """Split text containing HTML tables into table and non-table segments."""
        seg_pages: List[int] = [page_num] if page_num else []
        # Split around <table>...</table> blocks
        parts = re.split(r"(<table[\s\S]*?</table>)", text, flags=re.IGNORECASE)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if re.match(r"<table", part, re.IGNORECASE):
                segments.append({
                    "text": part,
                    "pages": list(seg_pages),
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
                    "pages": list(seg_pages),
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
                # Label the chunk with the heading it *starts* under, not the
                # last one it happens to run into.
                if not current_text or not current_header:
                    current_header = seg_header
                current_text = combined
                current_pages = list(set(current_pages + seg_pages))
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
        limit = self._limit_for(chunk_type)

        if _token_len(text) <= limit:
            return [Chunk(
                text=text.strip(),
                page_numbers=sorted(set(pages)),
                section_header=header,
                chunk_type=chunk_type,
            )]

        # An oversized table has no blank lines to split on, so the paragraph
        # path below would hand it to the sentence splitter and cut mid-row.
        if chunk_type == "table":
            row_chunks = self._split_table_rows(text, pages, header)
            if row_chunks:
                return row_chunks

        paragraphs = text.split("\n\n")
        chunks: List[Chunk] = []
        current = ""

        for para in paragraphs:
            candidate = f"{current}\n\n{para}" if current else para
            if _token_len(candidate) <= limit:
                current = candidate
            else:
                if current.strip():
                    chunks.append(Chunk(
                        text=current.strip(),
                        page_numbers=sorted(set(pages)),
                        section_header=header,
                        chunk_type=chunk_type,
                    ))
                if _token_len(para) > limit:
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

    def _split_table_rows(
        self, text: str, pages: List[int], header: str
    ) -> List[Chunk]:
        """Split a markdown table into row windows, repeating the header row.

        Returns [] when *text* has no detectable header row, so the caller can
        fall back to generic splitting.
        """
        lines = [ln for ln in text.split("\n") if ln.strip()]
        if not lines or not _TABLE_ROW_RE.match(lines[0].strip()):
            return []

        head_lines = [lines[0]]
        body_start = 1
        if len(lines) > 1 and (
            _TABLE_SEP_LINE_RE.match(lines[1].strip())
            or _TABLE_SEP_RE.match(lines[1].strip())
        ):
            head_lines.append(lines[1])
            body_start = 2

        body = lines[body_start:]
        if not body:
            return []

        head_text = "\n".join(head_lines)
        head_tokens = _token_len(head_text)
        limit = self.table_max_tokens
        page_numbers = sorted(set(pages))

        def build(rows: List[str]) -> Chunk:
            return Chunk(
                text=f"{head_text}\n" + "\n".join(rows),
                page_numbers=page_numbers,
                section_header=header,
                chunk_type="table",
            )

        chunks: List[Chunk] = []
        current: List[str] = []
        current_tokens = head_tokens

        for row in body:
            row_tokens = _token_len(row) + 1
            if current and current_tokens + row_tokens > limit:
                chunks.append(build(current))
                current = []
                current_tokens = head_tokens
            current.append(row)
            current_tokens += row_tokens

        if current:
            chunks.append(build(current))

        logger.debug("Split oversized table into %d row windows", len(chunks))
        return chunks

    def _force_split(
        self, text: str, pages: List[int], header: str, chunk_type: str
    ) -> List[Chunk]:
        limit = self._limit_for(chunk_type)
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks: List[Chunk] = []
        current = ""
        for sent in sentences:
            candidate = f"{current} {sent}" if current else sent
            if _token_len(candidate) <= limit:
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
        """Populate ``embed_text`` with boundary context from the previous chunk.

        ``text`` is left untouched: it is what gets stored, shown as the source
        excerpt, and fed to the LLM, so it must not carry duplicated content.
        """
        for i, chunk in enumerate(chunks):
            if i == 0 or self.overlap_tokens <= 0:
                chunk.embed_text = chunk.text
                continue

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

            chunk.embed_text = f"{overlap}\n\n{chunk.text}"

        return chunks


# ---------------------------------------------------------------------------
# EmbeddingCoreMixin — extraction + public API
# ---------------------------------------------------------------------------

class EmbeddingCoreMixin:
    """Core extraction/chunking methods used by EmbeddingService."""

    @staticmethod
    async def extract_text(s3_key: str, file_type: str) -> Optional[str]:
        """Extract document text.

        Every extractor here is synchronous and CPU/IO-heavy (OCR on a scanned
        PDF can run for minutes), so each runs in a worker thread — otherwise a
        single upload stalls the whole event loop, including live chat sockets.
        """
        content = await s3_client.get_file_content(s3_key)
        if not content:
            return None

        text = None

        if file_type == "pdf":
            # PDF: unstructured → pymupdf4llm → OCR
            text = await asyncio.to_thread(_extract_with_unstructured, content, file_type)
            if text and text.strip():
                logger.info("Extracted PDF with unstructured (%d chars) for %s", len(text), s3_key)
                return sanitize_text(text)

            logger.info("unstructured returned empty, trying pymupdf4llm for %s", s3_key)
            text = await asyncio.to_thread(_extract_pdf_with_pymupdf, content)
            if not text:
                logger.info("pymupdf4llm empty, trying OCR for %s", s3_key)
                text = await asyncio.to_thread(_extract_pdf_with_ocr, content)
        else:
            # Non-PDF: MarkItDown
            text = await asyncio.to_thread(_extract_with_markitdown, content, file_type)
            if text and text.strip():
                logger.info("Extracted with MarkItDown (%d chars) for %s", len(text), s3_key)
                return sanitize_text(text)

            # Fallback to unstructured if MarkItDown fails
            logger.info("MarkItDown returned empty, trying unstructured for %s", s3_key)
            text = await asyncio.to_thread(_extract_with_unstructured, content, file_type)

        return sanitize_text(text) if text else None

    @staticmethod
    def chunk_text(text: str) -> List[Chunk]:
        """Chunk document text into enriched Chunk objects (token-aware).

        Synchronous and token-heavy — call via :meth:`chunk_text_async` from
        async code.
        """
        chunker = SmartDocumentChunker(
            max_chunk_tokens=MAX_CHUNK_TOKENS,
            overlap_tokens=OVERLAP_TOKENS,
            min_chunk_tokens=MIN_CHUNK_TOKENS,
            table_max_chunk_tokens=TABLE_MAX_CHUNK_TOKENS,
        )
        return chunker.chunk_document(text)

    @staticmethod
    async def chunk_text_async(text: str) -> List[Chunk]:
        """Chunk in a worker thread — tokenizing a large document is not cheap."""
        return await asyncio.to_thread(EmbeddingCoreMixin.chunk_text, text)
