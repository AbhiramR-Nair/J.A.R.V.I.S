"""Pydantic models for parsed PDF papers.

These are data shapes only — no parsing logic lives here.
Consumed by backend/tools/pdf_parser.py (Day 22) and
backend/tools/summarize_paper.py (Day 23).
"""

from pydantic import BaseModel


class PaperSection(BaseModel):
    """A logical section of a paper (Abstract, Methods, Results, etc.).

    `heading` is the detected section header text.
    `body` is all the text between this heading and the next one.
    `page_start` is 1-indexed so it reads naturally in log messages.
    """

    heading: str
    body: str
    page_start: int


class PaperChunk(BaseModel):
    """A chunk of text sized for LLM consumption (~2000 chars).

    When section detection succeeds, chunks align to section boundaries.
    When it fails, chunks are fixed-size windows over full_text —
    source_heading is None in that case so Day 23 can label them "chunk N"
    instead of a heading that doesn't exist.
    """

    text: str
    source_heading: str | None  # None means fallback (fixed-window) chunking
    chunk_index: int
    char_count: int


class ParsedPaper(BaseModel):
    """Full output of parse_pdf(path).

    title and abstract are best-effort heuristic extractions — either may be
    None if detection fails. Day 23's summarizer works from chunks regardless.

    is_scanned=True means pymupdf found no text layer (image-only PDF). Day 23
    returns a graceful error message instead of attempting summarization.

    sections is empty when no headings were detected; chunks falls back to
    fixed-size windows over full_text in that case.
    """

    title: str | None
    abstract: str | None
    sections: list[PaperSection]
    chunks: list[PaperChunk]
    full_text: str
    page_count: int
    is_scanned: bool
    source_path: str  # absolute path string; for logging and Day 23 tool result
