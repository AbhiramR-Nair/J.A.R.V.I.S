"""PDF parsing for paper summarization.

Public entry point: parse_pdf(path). Returns a structured ParsedPaper with
title, abstract, sections, and chunks ready for LLM summarization (Day 23).

Handles three failure modes:
1. File missing / unreadable  -> FileNotFoundError or PDFParseError
2. Encrypted PDF              -> PDFParseError("encrypted")
3. No text layer (scanned)    -> ParsedPaper(is_scanned=True, sections=[], chunks=[])
   Caller decides what to do — Day 23 will surface a graceful message to the user.

NOT in scope for this module: LLM summarization, vector storage, voice integration.
Those live in summarize_paper.py (Day 23) and the orchestrator.

Not supported in v1 (deferred to v2 — see docs/journal.md Day 22 entry):
- OCR on scanned PDFs
- Figure / chart / table image extraction
- Equation image extraction (LaTeX equations appear as garbled text — acceptable)
- Multimodal summarization (sending PDF bytes to Gemini Vision)
"""

import re
from pathlib import Path

import fitz  # pymupdf — note: pip package is "pymupdf", import is "fitz"
from loguru import logger

from backend.models.pdf import ParsedPaper, PaperChunk, PaperSection


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class PDFParseError(Exception):
    """Raised when a PDF cannot be opened at all (encrypted, corrupt, etc.).

    Scanned PDFs do NOT raise — they return is_scanned=True. The distinction:
    PDFParseError = "couldn't open this file"; scanned = "opened fine, no text".
    """


# ---------------------------------------------------------------------------
# Compiled patterns (module-level so they are compiled once)
# ---------------------------------------------------------------------------

# Matches the word "Abstract" followed by body text, ending at the first
# numbered section, "Introduction", or "Keywords" line.
_ABSTRACT_RE = re.compile(
    r"(?:^|\n)\s*Abstract\s*\n+(.*?)(?=\n\s*(?:\d+[\.\s]|Introduction|Keywords)\b)",
    re.IGNORECASE | re.DOTALL,
)

# Matches common section header patterns found in academic papers:
#   "1. Introduction"  "2.1 Methods"  "3.2.1 Sub-section"
#   "METHODS"  "RESULTS AND DISCUSSION"
#   Bare keywords: "Abstract", "Introduction", "Methods", "Results",
#                  "Discussion", "Conclusion", "References", "Acknowledgments"
_SECTION_RE = re.compile(
    r"^(?:"
    # Numbered headers must be the only content on the line ("1. Introduction\n").
    # The \s*$ prevents matching "1. Introduction and background; see Fig 3" mid-sentence.
    r"(?:\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z ]{2,50})\s*$"
    r"|"
    # ALL-CAPS headers: at least 5 chars total (excludes DNA, PEG, USA etc.) and
    # must end the line (excludes inline abbreviations followed by body text).
    r"(?:[A-Z][A-Z ]{4,50})\s*$"
    r"|"
    # Bare well-known keywords are matched regardless of length — these are
    # unambiguous and always appear as standalone header lines in academic papers.
    r"(?:Abstract|Introduction|Methods?|Results?|Discussion|Conclusion|"
    r"References|Acknowledgments?|Background|Related Work|Limitations?)\s*$"
    r")",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_pdf(path: Path) -> ParsedPaper:
    """Parse a PDF into structured sections and chunks for LLM summarization.

    Args:
        path: Absolute path to the PDF file.

    Returns:
        ParsedPaper with title, abstract, sections, chunks, full_text.
        If the PDF has no text layer, returns is_scanned=True with empty
        sections and chunks — no exception raised.

    Raises:
        FileNotFoundError: if the file does not exist.
        PDFParseError:     if the file is encrypted, corrupt, or unreadable.
    """
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    # fitz.open raises fitz.FileDataError for corrupt/unreadable files.
    # Encrypted PDFs open without raising but set needs_pass=True — must check
    # explicitly, otherwise text extraction silently returns empty strings.
    try:
        doc = fitz.open(path)
    except fitz.FileDataError as exc:
        raise PDFParseError(f"corrupt or unreadable PDF: {exc}") from exc

    if doc.needs_pass:
        doc.close()
        raise PDFParseError("encrypted PDF — not supported in v1")

    try:
        return _parse_open_doc(doc, path)
    finally:
        # Always release the file handle, even if _parse_open_doc raises.
        doc.close()


# ---------------------------------------------------------------------------
# Internal — main parse logic (separated so the finally-close is clean)
# ---------------------------------------------------------------------------

def _parse_open_doc(doc: fitz.Document, path: Path) -> ParsedPaper:
    """Core parse logic. Called with an open, non-encrypted fitz.Document."""
    page_count = len(doc)

    # --- Extract full text ---------------------------------------------------
    # get_text("text") uses pymupdf's layout engine to reorder blocks by reading
    # order. For two-column papers this usually produces correct column order.
    full_text_parts: list[str] = []
    for page in doc:
        full_text_parts.append(page.get_text("text"))
    full_text = "\n".join(full_text_parts)

    # A PDF is "scanned" when the page is an image with no text layer.
    # Threshold: fewer than 100 chars across the whole document.
    if len(full_text.strip()) < 100:
        logger.warning(f"pdf appears to be scanned (no text layer): {path.name}")
        return ParsedPaper(
            title=None,
            abstract=None,
            sections=[],
            chunks=[],
            full_text="",
            page_count=page_count,
            is_scanned=True,
            source_path=str(path),
        )

    # --- Heuristic extraction ------------------------------------------------
    title = _extract_title(doc)
    abstract = _extract_abstract(full_text)
    sections = _detect_sections(full_text)
    chunks = _chunk_paper(sections, full_text)

    logger.info(
        f"parsed pdf: name={path.name} pages={page_count} "
        f"sections={len(sections)} chunks={len(chunks)} "
        f"title={'yes' if title else 'no'} abstract={'yes' if abstract else 'no'}"
    )

    return ParsedPaper(
        title=title,
        abstract=abstract,
        sections=sections,
        chunks=chunks,
        full_text=full_text,
        page_count=page_count,
        is_scanned=False,
        source_path=str(path),
    )


# ---------------------------------------------------------------------------
# Internal — title extraction
# ---------------------------------------------------------------------------

def _extract_title(doc: fitz.Document) -> str | None:
    """Heuristic: the title is the largest-font text in the top half of page 1.

    Uses get_text("dict") to access per-span font sizes. Falls back to the
    first non-empty line of full text if no large spans are found.
    """
    try:
        page = doc[0]
        page_height = page.rect.height
        blocks = page.get_text("dict")["blocks"]

        best_size = 0.0
        best_text = ""

        for block in blocks:
            # Only text blocks have a "lines" key; image blocks don't.
            if block.get("type") != 0:
                continue
            # Ignore blocks in the bottom half of the page (captions, footnotes).
            if block["bbox"][1] > page_height * 0.6:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    size = span.get("size", 0.0)
                    if text and size > best_size:
                        best_size = size
                        best_text = text

        if best_text:
            return best_text

    except Exception as exc:  # noqa: BLE001
        # Title is nice-to-have; never let heuristic failures crash the parser.
        logger.debug(f"title extraction failed: {exc}")

    # Fallback: first non-empty line of the document text.
    for line in doc[0].get_text("text").splitlines():
        line = line.strip()
        if line:
            return line

    return None


# ---------------------------------------------------------------------------
# Internal — abstract extraction
# ---------------------------------------------------------------------------

def _extract_abstract(full_text: str) -> str | None:
    """Heuristic: regex for text between 'Abstract' and the first section header.

    Works for ~80% of arxiv-style papers. Returns None silently on failure —
    Day 23's summarizer works from chunks regardless of whether abstract exists.
    """
    match = _ABSTRACT_RE.search(full_text)
    if match:
        abstract = match.group(1).strip()
        # Sanity check: if the captured text is very short or very long,
        # the regex likely matched noise rather than the real abstract.
        if 50 < len(abstract) < 3000:
            return abstract
    return None


# ---------------------------------------------------------------------------
# Internal — section detection
# ---------------------------------------------------------------------------

def _detect_sections(full_text: str) -> list[PaperSection]:
    """Split full_text into labelled sections using header regex.

    Returns a list ordered by appearance. If fewer than 2 headers are found,
    returns an empty list — the caller falls back to fixed-window chunking.
    """
    matches = list(_SECTION_RE.finditer(full_text))
    if len(matches) < 2:
        return []

    sections: list[PaperSection] = []
    lines_before = full_text[: matches[0].start()].count("\n")

    for i, match in enumerate(matches):
        heading = match.group(0).strip()
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        body = full_text[body_start:body_end].strip()

        # page_start: approximate from newline count up to this match position.
        # Not precise (pages differ in line count) but good enough for log display.
        newlines_to_here = full_text[: match.start()].count("\n")
        # Assume ~50 lines per page as a rough heuristic.
        page_start = max(1, newlines_to_here // 50 + 1)

        # Skip sections with trivial bodies — these are false positives from
        # abbreviations (PEG, USA) or numbered list items matching the header
        # regex. A real section always has meaningful content.
        if len(body.strip()) >= 100:
            sections.append(PaperSection(
                heading=heading,
                body=body,
                page_start=page_start,
            ))

    return sections


# ---------------------------------------------------------------------------
# Internal — chunking
# ---------------------------------------------------------------------------

def _chunk_paper(
    sections: list[PaperSection],
    full_text: str,
    target_size: int = 2000,
) -> list[PaperChunk]:
    """Produce LLM-ready chunks from sections (preferred) or full_text (fallback).

    Section-aligned: short sections → one chunk; long sections → split at
    paragraph boundaries. Fallback: fixed windows over full_text.
    target_size=2000 chars ≈ 500–700 tokens, comfortably within Gemini Flash context.
    """
    chunks: list[PaperChunk] = []
    idx = 0

    if len(sections) >= 2:
        # Section-aligned chunking: each section becomes one or more chunks.
        for section in sections:
            if len(section.body) <= target_size:
                chunks.append(PaperChunk(
                    text=section.body,
                    source_heading=section.heading,
                    chunk_index=idx,
                    char_count=len(section.body),
                ))
                idx += 1
            else:
                # Long section: split at paragraph boundaries.
                for window in _split_on_paragraphs(section.body, target_size):
                    chunks.append(PaperChunk(
                        text=window,
                        source_heading=section.heading,
                        chunk_index=idx,
                        char_count=len(window),
                    ))
                    idx += 1
    else:
        # Fallback: no sections detected — chunk full_text into fixed windows.
        for window in _split_on_paragraphs(full_text, target_size):
            chunks.append(PaperChunk(
                text=window,
                source_heading=None,  # signals fallback chunking to Day 23
                chunk_index=idx,
                char_count=len(window),
            ))
            idx += 1

    return chunks


def _split_on_paragraphs(text: str, target_size: int) -> list[str]:
    """Greedy split: accumulate paragraphs until target_size, then start a new chunk.

    Splits on blank lines (double newline). Never cuts mid-paragraph, so chunks
    may exceed target_size by one paragraph length — acceptable for our use case
    since Gemini Flash has a 1M token context.
    """
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_size = len(para)
        if current_size + para_size > target_size and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_size = para_size
        else:
            current.append(para)
            current_size += para_size

    if current:
        chunks.append("\n\n".join(current))

    return chunks
