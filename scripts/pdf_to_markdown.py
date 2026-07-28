#!/usr/bin/env python3
"""Convert a local PDF to markdown using the same extractor the RAG pipeline uses.

Runs entirely offline — no S3, no database, no embeddings. Useful for eyeballing
what the indexer will actually see before a document is ingested.

Usage:
    python scripts/pdf_to_markdown.py report.pdf
    python scripts/pdf_to_markdown.py report.pdf -o /tmp/out.md
    python scripts/pdf_to_markdown.py *.pdf --outdir converted/
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.embedding_core import _extract_pdf, _extract_with_markitdown  # noqa: E402
from src.utils.text_utils import sanitize_text  # noqa: E402

logger = logging.getLogger("pdf2md")


def convert(path: Path) -> str:
    """Extract *path* to markdown. Raises RuntimeError when nothing comes out."""
    file_bytes = path.read_bytes()
    suffix = path.suffix.lstrip(".").lower()

    # Same split as the ingest pipeline: PDFs through pymupdf4llm (which OCRs
    # its own empty pages), everything else through markitdown.
    if suffix == "pdf":
        text = _extract_pdf(file_bytes)
    else:
        text = _extract_with_markitdown(file_bytes, suffix)

    if not text or not text.strip():
        raise RuntimeError(
            f"extracted nothing from {path.name} — it may be an image-only PDF "
            f"with no OCR available (is the tesseract binary installed?)"
        )
    return sanitize_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path, help="PDF (or docx/pptx/xlsx…) to convert")
    parser.add_argument("-o", "--output", type=Path,
                        help="Output file. Only valid with a single input; "
                             "defaults to the input name with a .md suffix.")
    parser.add_argument("--outdir", type=Path, help="Write all outputs into this directory")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress extractor logs")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.ERROR if args.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if args.output and len(args.files) > 1:
        parser.error("-o/--output takes a single input file; use --outdir for several")
    if args.outdir:
        args.outdir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for path in args.files:
        if not path.is_file():
            logger.error("%s: not a file", path)
            failures += 1
            continue

        try:
            markdown = convert(path)
        except Exception as e:
            logger.error("%s: %s", path.name, e)
            failures += 1
            continue

        if args.output:
            destination = args.output
        elif args.outdir:
            destination = args.outdir / f"{path.stem}.md"
        else:
            destination = path.with_suffix(".md")

        destination.write_text(markdown, encoding="utf-8")
        # Page markers are HTML comments, so they stay invisible when the
        # markdown is rendered but still show which page a passage came from.
        pages = markdown.count("<!-- page ")
        print(f"{path.name} → {destination}  ({len(markdown):,} chars, {pages} pages)")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
