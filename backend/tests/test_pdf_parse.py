"""Smoke test for PDF parsing — run manually after Day 22 work.

Usage:
    python -m backend.tests.test_pdf_parse

Prints a structured summary for every PDF in data/test_pdfs/.
No assertions — eyeball the output to verify section detection and chunking
look sensible before wiring into the LLM on Day 23.
"""

from pathlib import Path

from backend.tools.pdf_parser import PDFParseError, parse_pdf

TEST_PDF_DIR = Path("data/test_pdfs")


def main() -> None:
    pdfs = sorted(TEST_PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {TEST_PDF_DIR}. Add test files and re-run.")
        return

    for pdf in pdfs:
        print(f"\n{'=' * 70}")
        print(f"PDF: {pdf.name}")
        print("=" * 70)

        try:
            result = parse_pdf(pdf)
        except FileNotFoundError as exc:
            print(f"  ERROR (file not found): {exc}")
            continue
        except PDFParseError as exc:
            print(f"  ERROR (parse failed): {exc}")
            continue

        print(f"  pages   : {result.page_count}")
        print(f"  scanned : {result.is_scanned}")

        title_display = (result.title or "<none detected>")[:90]
        print(f"  title   : {title_display}")

        if result.abstract:
            abstract_preview = result.abstract[:200].replace("\n", " ")
            print(f"  abstract: {abstract_preview}...")
        else:
            print("  abstract: <none detected>")

        print(f"\n  sections ({len(result.sections)} detected):")
        if result.sections:
            for sec in result.sections[:8]:
                print(f"    [{sec.page_start:>3}p] {sec.heading[:55]:<55} ({len(sec.body):>5} chars)")
            if len(result.sections) > 8:
                print(f"    ... +{len(result.sections) - 8} more sections")
        else:
            print("    (none — fallback chunking active)")

        print(f"\n  chunks ({len(result.chunks)} total, target ~2000 chars each):")
        for chunk in result.chunks[:4]:
            heading = (chunk.source_heading or "<fallback>")[:30]
            preview = chunk.text[:80].replace("\n", " ")
            print(f"    [{chunk.chunk_index}] {heading:<30} {chunk.char_count:>5} chars | {preview}...")
        if len(result.chunks) > 4:
            print(f"    ... +{len(result.chunks) - 4} more chunks")

        # Sanity numbers
        if result.chunks:
            max_chunk = max(c.char_count for c in result.chunks)
            avg_chunk = sum(c.char_count for c in result.chunks) // len(result.chunks)
            print(f"\n  chunk stats: max={max_chunk} chars  avg={avg_chunk} chars")


if __name__ == "__main__":
    main()
