"""Manual smoke test for the summarize_paper tool.

Run from the repo root:
    python -m backend.tests.test_summarize_paper

Tests both test PDFs in data/test_pdfs/ and the common error paths.
No assertions — read the output and verify it looks sensible.
"""

import asyncio
from pathlib import Path

from backend.tools.summarize_paper import summarize_paper


async def main() -> None:
    test_dir = Path("data/test_pdfs")
    pdfs = list(test_dir.glob("*.pdf"))

    if not pdfs:
        print(f"No PDFs found in {test_dir}. Add test PDFs and re-run.")
        return

    # --- Real PDFs ---
    for i, pdf in enumerate(pdfs):
        if i > 0:
            # Wait between papers so rate limits reset after a heavy map-reduce run.
            print("\n[waiting 45s for rate limits to reset before next paper...]\n")
            await asyncio.sleep(45)
        print(f"\n{'=' * 60}")
        print(f"PDF: {pdf.name}")
        print("=" * 60)
        result = await summarize_paper(str(pdf))

        if "error" in result:
            print(f"ERROR ({result['type']}): {result['error']}")
            continue

        print(f"Title:     {result['title']}")
        print(f"Pages:     {result['_meta']['pages']}")
        print(f"Claims ({len(result['key_claims'])}):")
        for claim in result["key_claims"]:
            print(f"  - {claim}")
        print(f"Methods:   {result['methods'][:200]}...")
        print(f"Results:   {result['results'][:200]}...")
        print(f"Limits:    {result['limitations'][:150]}...")
        print(f"Relevance: {result['relevance_to_user']}")

    # --- Error paths ---
    print(f"\n{'=' * 60}")
    print("Error path: bad path")
    print("=" * 60)
    r = await summarize_paper("nonexistent/path/paper.pdf")
    print(f"({r['type']}) {r['error']}")

    print(f"\n{'=' * 60}")
    print("Error path: corrupt file (txt renamed .pdf)")
    print("=" * 60)
    corrupt = Path("data/test_pdfs/_corrupt_test.pdf")
    corrupt.write_text("this is not a pdf", encoding="utf-8")
    try:
        r = await summarize_paper(str(corrupt))
        print(f"({r['type']}) {r['error']}")
    finally:
        corrupt.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
