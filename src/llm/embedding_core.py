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


# markitdown annotates pptx slides as `<!-- Slide number: 3 -->`. A slide is the
# pptx equivalent of a page and is what a citation should point at, so it is
# rewritten into the marker the chunker understands.
_SLIDE_MARKER_RE = re.compile(r"<!--\s*Slide number:\s*(\d+)\s*-->", re.IGNORECASE)


def _normalize_page_markers(text: str) -> str:
    return _SLIDE_MARKER_RE.sub(lambda m: _page_marker(int(m.group(1))), text)


# ---------------------------------------------------------------------------
# HTML table → markdown
#
# markitdown runs docx/html through markdownify, which passes tables through as
# HTML. Embedding tag soup wastes tokens and dilutes the vector, so tables are
# normalised to markdown, which the chunker can also split row-wise while
# repeating the header.
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
# Extraction via `markitdown` — every non-PDF format
#
# markitdown's format support is extras-gated: docx needs mammoth, pptx needs
# python-pptx, xlsx needs openpyxl+pandas. requirements.txt pins
# markitdown[docx,pptx,xlsx,xls,outlook]; without those extras these formats
# raise here and the document indexes as failed rather than silently empty.
# ---------------------------------------------------------------------------

def _extract_with_markitdown(file_bytes: bytes, file_type: str) -> Optional[str]:
    """Extract text from non-PDF documents using MarkItDown."""
    tmp_path: Optional[str] = None
    try:
        from markitdown import MarkItDown

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        md = MarkItDown()
        result = md.convert(tmp_path)
        text = result.text_content

        return _normalize_page_markers(text) if text and text.strip() else None

    except Exception as e:
        logger.warning("MarkItDown extraction failed for .%s: %s", file_type, e)
        return None
    finally:
        # In the original the unlink lived in an inner `finally` that was never
        # reached when NamedTemporaryFile itself raised, leaking the file.
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Extraction via `pymupdf4llm` — PDFs
#
# Chosen over `unstructured`'s hi_res path: native C, no layout model to
# download at runtime (a cold-start trap in a container with no persistent
# volume), and 10–50x faster. It emits markdown with headings inferred from
# font size and tables via PyMuPDF's table finder, which is exactly what the
# chunker downstream expects.
#
# Pages that come back near-empty are scanned images, so they are OCR'd
# individually. Testing yield *per page* rather than per document matters: a
# stray text layer over a scanned deck otherwise passes as extracted.
# ---------------------------------------------------------------------------

# Below this many characters a page is treated as unextracted and sent to OCR.
_MIN_CHARS_PER_PAGE = 100

try:
    # The pymupdf-layout distribution installs into the pymupdf namespace as
    # pymupdf.layout. pymupdf4llm picks it up automatically; importing it here
    # makes the dependency explicit and its absence loggable. Its ONNX layout
    # models ship inside the wheel, so there is no download on first use.
    import pymupdf.layout  # noqa: F401
    _LAYOUT_AVAILABLE = True
except ImportError:
    _LAYOUT_AVAILABLE = False
    logger.warning(
        "pymupdf-layout not installed — multi-column PDFs may extract out of "
        "reading order. Install it to improve layout analysis."
    )


def _ocr_page(page) -> str:
    """OCR a single page. Returns '' when tesseract is unavailable or finds nothing."""
    try:
        tp = page.get_textpage_ocr(language="eng", dpi=300, full=True)
        return (page.get_text("text", textpage=tp) or "").strip()
    except Exception as e:
        # Most often the tesseract binary is missing from the image
        logger.debug("OCR failed on page: %s", e)
        return ""


def _extract_pdf(file_bytes: bytes) -> Optional[str]:
    """Extract a PDF to page-marked markdown, OCR'ing pages that need it."""
    try:
        import pymupdf
        import pymupdf4llm
    except ImportError as e:
        logger.error("PDF extraction unavailable — pymupdf4llm missing: %s", e)
        return None

    doc = None
    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        try:
            pages = pymupdf4llm.to_markdown(doc, page_chunks=True)
        except Exception as e:
            # Malformed PDFs can break the markdown writer while the raw text
            # layer is still readable — fall back to it rather than losing the
            # document entirely.
            logger.warning("pymupdf4llm markdown conversion failed (%s) — using raw text", e)
            pages = [
                {"text": p.get_text("text"), "metadata": {"page": i}}
                for i, p in enumerate(doc, 1)
            ]

        parts: List[str] = []
        ocr_pages = 0

        for i, page in enumerate(pages, 1):
            page_text = (page.get("text") or "").strip()
            page_no = (page.get("metadata") or {}).get("page") or i

            if len(page_text) < _MIN_CHARS_PER_PAGE and 0 < page_no <= doc.page_count:
                ocr_text = _ocr_page(doc[page_no - 1])
                # Keep whichever read more — OCR on a genuinely sparse page
                # (a section divider, a full-page figure) can return noise.
                if len(ocr_text) > len(page_text):
                    page_text = ocr_text
                    ocr_pages += 1

            if not page_text:
                continue
            parts.append(f"{_page_marker(page_no)}\n{page_text}")

        if ocr_pages:
            logger.info("OCR'd %d/%d PDF pages with no usable text layer", ocr_pages, len(pages))

        md_text = "\n\n".join(parts)
        return md_text if md_text.strip() else None
    except Exception as e:
        logger.warning("PDF extraction failed: %s", e)
        return None
    finally:
        if doc is not None:
            doc.close()


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

_CODE_FENCE_RE = re.compile(r"^(?:```|~~~)")
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
# Repeated running headers / footers
# ---------------------------------------------------------------------------

def _strip_repeated_lines(page_blocks: List[Dict], min_pages: int = 4) -> List[Dict]:
    """Drop lines that repeat on most pages — running headers, footers, stamps.

    PyMuPDF keeps them on every page, so without this each chunk carries a copy
    of "ACME Corp — Confidential" and the noise dilutes both the embedding and
    the excerpt the user is shown.

    Only applied to documents with real page structure and enough pages for a
    repeat to be evidence rather than coincidence.
    """
    paged = [b for b in page_blocks if b["page"] is not None]
    if len(paged) < min_pages:
        return page_blocks

    counts: Dict[str, int] = {}
    for block in paged:
        # Headers/footers sit at the page edges; a line repeated in the body is
        # more likely to be real content (a table row, a bullet).
        lines = [ln.strip() for ln in block["text"].split("\n") if ln.strip()]
        for line in set(lines[:3] + lines[-3:]):
            counts[line] = counts.get(line, 0) + 1

    threshold = max(min_pages - 1, int(len(paged) * 0.6))
    boilerplate = {
        line for line, n in counts.items()
        # Long lines are prose that happens to repeat, not a running header
        if n >= threshold and len(line) <= 120
    }
    if not boilerplate:
        return page_blocks

    logger.debug("Stripping %d repeated header/footer lines", len(boilerplate))

    cleaned: List[Dict] = []
    for block in page_blocks:
        text = "\n".join(
            ln for ln in block["text"].split("\n") if ln.strip() not in boilerplate
        ).strip()
        if text:
            cleaned.append({**block, "text": text})
    return cleaned


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
    - HTML table support (from markitdown's markdownify output)
    - Fenced code blocks kept atomic
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
        page_blocks = _strip_repeated_lines(_annotate_pages(text))
        raw_segments = self._segment_by_headings_and_tables(page_blocks)
        chunks = self._merge_segments(raw_segments)

        # Drop undersized chunks *before* overlap, so borderline junk can't be
        # rescued by text borrowed from its neighbour. Tables and code are
        # exempt: a two-row table or a four-line snippet is short but is often
        # the whole answer.
        final = [
            c for c in chunks
            if c.chunk_type in ("table", "code") or _token_len(c.text) >= self.min_tokens
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

            # Handle HTML tables (markitdown routes docx/html through
            # markdownify, which emits tables as HTML): split them out first
            if _is_html_table_block(text):
                self._split_html_tables(text, page_num, tracker, segments)
                continue

            lines = text.split("\n")
            current_lines: List[str] = []
            current_type = "text"
            in_table = False
            in_code = False

            def flush(chunk_type: str):
                if current_lines:
                    segments.append({
                        "text": "\n".join(current_lines).strip(),
                        "pages": list(seg_pages),
                        "section_header": tracker.current,
                        "chunk_type": chunk_type,
                    })

            for line in lines:
                # Fenced code is atomic: everything inside it — '#' comments,
                # '|' pipes in a shell command — must not be read as markdown,
                # and the sentence splitter must never cut through it.
                if _CODE_FENCE_RE.match(line.strip()):
                    if in_code:
                        current_lines.append(line)
                        flush("code")
                        current_lines = []
                        current_type = "text"
                        in_code = False
                    else:
                        flush(current_type)
                        current_lines = [line]
                        current_type = "code"
                        in_code = True
                        in_table = False
                    continue

                if in_code:
                    current_lines.append(line)
                    continue

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
                # Normalise to markdown so the row-window splitter can handle an
                # oversized table, and so the stored chunk is not raw tag soup.
                segments.append({
                    "text": _html_table_to_markdown(part) or part,
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
        # Code has no sentences — splitting it on '.' cuts through method chains
        # and file paths. Break on line boundaries instead.
        if chunk_type == "code":
            pieces = text.split("\n")
            joiner = "\n"
        else:
            pieces = re.split(r'(?<=[.!?])\s+', text)
            joiner = " "
        chunks: List[Chunk] = []
        current = ""
        for sent in pieces:
            candidate = f"{current}{joiner}{sent}" if current else sent
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

            prev = chunks[i - 1]
            # Overlap only carries context when the neighbour is actually
            # continuous prose. Borrowing the tail of a different section, or of
            # a table or code block, injects unrelated tokens into the vector.
            if (
                prev.section_header != chunk.section_header
                or prev.chunk_type != "text"
                or chunk.chunk_type != "text"
            ):
                chunk.embed_text = chunk.text
                continue

            prev_text = prev.text
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

        One extractor per format family, no fallback chain between them:
          PDF     → pymupdf4llm, with per-page OCR where the text layer is empty
          non-PDF → markitdown

        Both are synchronous and CPU/IO-heavy (OCR on a scanned PDF can run for
        minutes), so they run in a worker thread — otherwise a single upload
        stalls the whole event loop, including live chat sockets.
        """
        content = await s3_client.get_file_content(s3_key)
        if not content:
            return None

        if file_type == "pdf":
            text = await asyncio.to_thread(_extract_pdf, content)
            extractor = "pymupdf4llm"
        else:
            text = await asyncio.to_thread(_extract_with_markitdown, content, file_type)
            extractor = "markitdown"

        if not text or not text.strip():
            logger.warning("%s extracted nothing from %s (.%s)", extractor, s3_key, file_type)
            return None

        logger.info("Extracted %s with %s (%d chars)", s3_key, extractor, len(text))
        return sanitize_text(text)

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
