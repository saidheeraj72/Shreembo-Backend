"""
Test script: PDF → Markdown → Chunks → Output file
Reads a PDF, extracts text, chunks it, and writes results to chunks_output.txt

Usage:
    python test_chunking.py
    python test_chunking.py /path/to/your.pdf
"""
import sys
import json

# ── Hardcode your PDF path here ──────────────────────────────────────────
PDF_PATH = "/Users/saidheeraj/Desktop/test.pdf"
# ─────────────────────────────────────────────────────────────────────────

OUTPUT_FILE = "chunks_output.txt"


def extract_pdf(pdf_path: str) -> str:
    """Extract text using the full extraction chain: unstructured → pymupdf4llm → OCR."""
    with open(pdf_path, "rb") as f:
        file_bytes = f.read()

    file_type = pdf_path.rsplit(".", 1)[-1].lower() if "." in pdf_path else "pdf"

    print(f"File size: {len(file_bytes):,} bytes")

    if file_type == "pdf":
        import pymupdf
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        print(f"Pages: {len(doc)}")
        doc.close()

    # 1. Try unstructured (best quality)
    from src.llm.embedding_core import _extract_with_unstructured
    text = _extract_with_unstructured(file_bytes, file_type)
    if text and text.strip():
        print(f"Extraction: unstructured ({len(text):,} chars)")
        return text

    # 2. Fallback: pymupdf4llm
    if file_type == "pdf":
        from src.llm.embedding_core import _extract_pdf_with_pymupdf
        text = _extract_pdf_with_pymupdf(file_bytes)
        if text and text.strip():
            print(f"Extraction: pymupdf4llm fallback ({len(text):,} chars)")
            return text

        # 3. Last resort: OCR
        from src.llm.embedding_core import _extract_pdf_with_ocr
        text = _extract_pdf_with_ocr(file_bytes)
        if text and text.strip():
            print(f"Extraction: OCR fallback ({len(text):,} chars)")
            return text

    raise RuntimeError(f"All extraction methods failed for {pdf_path}")


def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else PDF_PATH

    print(f"{'=' * 70}")
    print(f"PDF Chunking Test")
    print(f"{'=' * 70}")
    print(f"Input:  {pdf_path}")
    print(f"Output: {OUTPUT_FILE}")
    print()

    # ── Step 1: Extract ──────────────────────────────────────────────────
    print("─── Step 1: Extracting text from PDF ───")
    raw_text = extract_pdf(pdf_path)
    print(f"Extracted text: {len(raw_text):,} chars")
    print()

    # ── Step 2: Chunk ────────────────────────────────────────────────────
    print("─── Step 2: Chunking with SmartDocumentChunker (token-aware) ───")
    from src.llm.embedding_core import SmartDocumentChunker, _token_len

    chunker = SmartDocumentChunker(
        max_chunk_tokens=512,
        overlap_tokens=50,
        min_chunk_tokens=30,
    )
    chunks = chunker.chunk_document(raw_text)
    print(f"Total chunks: {len(chunks)}")

    # Stats
    sizes = [len(c.text) for c in chunks]
    token_counts = [_token_len(c.text) for c in chunks]
    types = {}
    for c in chunks:
        types[c.chunk_type] = types.get(c.chunk_type, 0) + 1
    print(f"Chunk sizes (chars): min={min(sizes)}, max={max(sizes)}, avg={sum(sizes)//len(sizes)}")
    print(f"Chunk sizes (tokens): min={min(token_counts)}, max={max(token_counts)}, avg={sum(token_counts)//len(token_counts)}")
    print(f"Chunk types: {types}")
    chunks_with_headers = sum(1 for c in chunks if c.section_header)
    chunks_with_pages = sum(1 for c in chunks if c.page_numbers)
    chunks_with_overlap = sum(1 for c in chunks if c.text.startswith("[...]"))
    print(f"With section headers: {chunks_with_headers}/{len(chunks)}")
    print(f"With page numbers: {chunks_with_pages}/{len(chunks)}")
    print(f"With overlap: {chunks_with_overlap}/{len(chunks)}")
    print()

    # ── Step 3: Write output ─────────────────────────────────────────────
    print(f"─── Step 3: Writing to {OUTPUT_FILE} ───")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(f"PDF Chunking Results: {pdf_path}\n")
        f.write(f"{'=' * 70}\n")
        f.write(f"Total chunks: {len(chunks)}\n")
        f.write(f"Chunk sizes: min={min(sizes)}, max={max(sizes)}, avg={sum(sizes)//len(sizes)}\n")
        f.write(f"Chunk types: {types}\n\n")

        # Raw markdown preview (first 3000 chars)
        f.write(f"{'=' * 70}\n")
        f.write(f"RAW EXTRACTED MARKDOWN (first 3000 chars)\n")
        f.write(f"{'=' * 70}\n\n")
        f.write(raw_text[:3000])
        f.write("\n\n... [truncated]\n\n")

        # Each chunk in detail
        for c in chunks:
            f.write(f"{'=' * 70}\n")
            f.write(f"CHUNK {c.chunk_index}\n")
            f.write(f"{'=' * 70}\n")
            f.write(f"  Type:           {c.chunk_type}\n")
            f.write(f"  Section:        {c.section_header or '(none)'}\n")
            f.write(f"  Pages:          {c.page_numbers or '(unknown)'}\n")
            f.write(f"  Length:         {len(c.text)} chars / {_token_len(c.text)} tokens\n")
            f.write(f"  Has overlap:    {c.text.startswith('[...]')}\n")
            f.write(f"{'─' * 70}\n")
            f.write(c.text)
            f.write(f"\n{'─' * 70}\n\n")

        # JSON dump for programmatic inspection
        f.write(f"\n{'=' * 70}\n")
        f.write(f"JSON DUMP (for programmatic use)\n")
        f.write(f"{'=' * 70}\n\n")
        json_data = [
            {
                "chunk_index": c.chunk_index,
                "chunk_type": c.chunk_type,
                "section_header": c.section_header,
                "page_numbers": c.page_numbers,
                "char_length": len(c.text),
                "text": c.text,
            }
            for c in chunks
        ]
        f.write(json.dumps(json_data, indent=2, ensure_ascii=False))

    print(f"Done! Open {OUTPUT_FILE} to inspect the chunks.")
    print()

    # ── Quick preview in terminal ────────────────────────────────────────
    print(f"─── Preview (first 3 chunks) ───")
    for c in chunks[:3]:
        print(f"\n{'─' * 50}")
        print(f"Chunk {c.chunk_index} | {c.chunk_type} | pages={c.page_numbers}")
        print(f"Section: {c.section_header or '(none)'}")
        print(f"{'─' * 50}")
        preview = c.text[:300]
        if len(c.text) > 300:
            preview += "..."
        print(preview)


if __name__ == "__main__":
    main()
