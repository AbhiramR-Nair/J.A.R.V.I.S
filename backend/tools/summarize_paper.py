"""Tool: read a research paper from a local PDF and produce a structured summary.

Thin handler over pdf_parser + services/summarizer. All the actual work lives in
those modules; this file wires them to the tool registry so Gemini can call them
by voice.

Error handling strategy (from tool-calling-pattern/SKILL.md §"Hard vs soft errors"):
- FileNotFoundError, PDFParseError, ScannedPDF, SummarizationError → soft error dict.
  The LLM reads the error and speaks a human-friendly message. No exception raised.
- Unexpected exceptions → re-raise. The orchestrator routes to ERROR state.
"""

from pathlib import Path

from loguru import logger

from backend.memory import sqlite_store, vector_store
from backend.models.summary import PaperSummary
from backend.services.summarizer import SummarizationError, summarize_paper_text
from backend.tools import registry
from backend.tools.pdf_parser import PDFParseError, parse_pdf


def _resolve_path(raw: str) -> Path:
    """Return the best matching PDF path for a possibly-spoken file reference.

    STT renders paths as natural speech ("data test PDFs gene expression…"), so
    the LLM may pass a string that doesn't match the filesystem exactly.
    Strategy:
    1. Try the path as-is (covers exact paths and correct relative paths).
    2. If not found, score every PDF in data/test_pdfs/ by how many words from
       the raw string appear in the filename — return the best match if ≥2 words hit.
    3. Otherwise raise FileNotFoundError so the tool returns a clear soft error.
    """
    p = Path(raw)
    if p.exists():
        return p

    # Fuzzy match against known PDF directories (voice path-passing aid).
    # data/arxiv/ added in Day 24 — do NOT add data/ as a catch-all (too broad).
    search_dirs = [Path("data/test_pdfs"), Path("data/arxiv")]
    terms = [w for w in raw.lower().replace("/", " ").split() if len(w) > 2]
    best: Path | None = None
    best_score = 0
    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for candidate in search_dir.glob("*.pdf"):
            stem = candidate.stem.lower()
            score = sum(1 for t in terms if t in stem)
            if score > best_score:
                best_score = score
                best = candidate

    if best and best_score >= 2:
        logger.info(f"summarize_paper: fuzzy resolved '{raw}' -> '{best}'")
        return best

    raise FileNotFoundError(raw)


@registry.register(
    name="summarize_paper",
    description=(
        "Read a research paper from a local PDF file and produce a structured "
        "summary with title, key claims, methods, results, limitations, and "
        "relevance to the user's active project. "
        "Use this when the user says 'summarize this paper', 'summarize the PDF at <path>', "
        "or drops a PDF onto the window and asks to summarize it. "
        "If the user says 'summarize this' or 'summarize the dropped PDF' without giving "
        "a path, pass path='dropped' — the tool will locate the recently dropped file. "
        "Always call this tool — never summarize from memory."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or repo-relative path to the PDF file.",
            },
        },
        "required": ["path"],
    },
)
async def summarize_paper(path: str) -> dict:
    """Parse a PDF then summarize it; return a dict the LLM can read aloud."""
    # --- Parse ---
    # If path resolution fails, fall back to any pending drag-and-drop path before
    # returning an error. Gemini often passes a guessed path ("data/test.pdf",
    # "dropped.pdf") that doesn't exist when the user says "summarize this" or
    # "summarize the paper I dropped" — the pending drop covers all such cases.
    from backend.services.pending_drop import consume_pending_pdf
    try:
        resolved = _resolve_path(path)
        parsed = parse_pdf(resolved)
    except FileNotFoundError:
        pending = consume_pending_pdf()
        if pending is not None:
            logger.info(f"summarize_paper: path '{path}' not found, using pending drop '{pending}'")
            try:
                parsed = parse_pdf(pending)
            except PDFParseError as e:
                return {"error": f"Couldn't open the dropped PDF: {e}", "type": "PDFParseError"}
        else:
            return {"error": f"No file found at '{path}'.", "type": "FileNotFoundError"}
    except PDFParseError as e:
        return {"error": f"Couldn't open the PDF: {e}", "type": "PDFParseError"}

    if parsed.is_scanned:
        return {
            "error": (
                "That PDF appears to be scanned — it has no text layer I can read. "
                "OCR support is planned for a future version."
            ),
            "type": "ScannedPDF",
        }

    # --- Summarize ---
    try:
        summary: PaperSummary = await summarize_paper_text(parsed)
    except SummarizationError as e:
        return {"error": str(e), "type": "SummarizationError"}

    # --- Persist to project memory ---
    # Hard-coded importance=10: an explicit paper summary is always worth keeping.
    # Non-fatal: the user still gets the summary even if the memory write fails.
    memory_text = (
        f"Paper summary — {summary.title}\n"
        f"Key claims: {'; '.join(summary.key_claims)}\n"
        f"Methods: {summary.methods}\n"
        f"Results: {summary.results}\n"
        f"Limitations: {summary.limitations}\n"
        f"Source: {Path(parsed.source_path).name}"
    )
    try:
        project = sqlite_store.get_active_project()
        chroma_id = await vector_store.add(
            memory_text,
            project_id=project["id"],
            metadata={
                "importance": 10,
                "kind": "paper_summary",
                "source_path": str(parsed.source_path),
                "pages": parsed.page_count,
            },
        )
        sqlite_store.save_memory(
            text=memory_text,
            project_id=project["id"],
            importance=10,
            chroma_id=chroma_id,
        )
        logger.info(f"summarize_paper: summary saved to project '{project['name']}'")
    except Exception as e:
        logger.warning(f"summarize_paper: memory save failed (non-fatal): {e}")

    # Return the full summary dict + metadata for the chat panel.
    # The LLM reads key_claims + relevance_to_user aloud; the rest renders in the UI.
    result = summary.model_dump()
    result["_meta"] = {
        "pages": parsed.page_count,
        "source_path": str(parsed.source_path),
    }
    return result
