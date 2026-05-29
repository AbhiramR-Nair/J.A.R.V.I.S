"""Tool: fetch a paper by arxiv ID and produce a structured summary.

One combined tool (Option A): fetches AND summarizes in a single LLM tool-call.
This gives the cleanest voice UX — "summarize arxiv 2403.12345" triggers one tool,
one round-trip, one spoken response.

Do NOT use this to summarize local PDFs — that's summarize_paper's job.
Do NOT call summarize_paper from inside this handler — call summarize_paper_text()
(the service function) directly. Tool-calling-a-tool is not supported by the registry.

Error handling: all expected failures return soft dicts {"error": ..., "type": ...}.
Unexpected exceptions re-raise so the orchestrator can route to ERROR state.
"""

import asyncio
import re
from pathlib import Path

import arxiv
import httpx
from loguru import logger

from backend.memory import sqlite_store, vector_store
from backend.models.summary import PaperSummary
from backend.services.summarizer import SummarizationError, summarize_paper_text
from backend.tools import registry
from backend.tools.pdf_parser import PDFParseError, parse_pdf

# Matches arxiv IDs in the forms users actually say or type:
#   2403.12345          (standard post-2007 format)
#   2403.12345v2        (with version suffix — preserved through download)
#   arXiv:2403.12345    (with prefix — stripped before API call)
#   cond-mat/0411123    (pre-2007 subject/YYMM.NNN — rare but valid)
_ARXIV_ID_RE = re.compile(
    r"^(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+/\d{7})$",
    re.IGNORECASE,
)

# Timeout for the PDF HTTP download. 60s is generous — arxiv PDFs are rarely >10 MB
# and the connection is typically fast, but large papers with many figures can be slow.
_DOWNLOAD_TIMEOUT_SECONDS = 60


def _normalize_id(raw: str) -> str | None:
    """Strip whitespace and the 'arXiv:' prefix; return None if the format is wrong."""
    cleaned = raw.strip()
    m = _ARXIV_ID_RE.match(cleaned)
    if not m:
        return None
    # Group 1 captures the ID without the prefix.
    return m.group(1)


def _fetch_result_sync(arxiv_id: str) -> arxiv.Result:
    """Blocking call to the arxiv API. Must be run in a thread via run_in_executor.

    arxiv.Client().results() is synchronous. Calling it directly from an async
    function would block the entire FastAPI event loop for up to ~3s (the library's
    built-in politeness delay). run_in_executor offloads it to a thread pool thread.
    """
    client = arxiv.Client(page_size=1, delay_seconds=3.0, num_retries=3)
    search = arxiv.Search(id_list=[arxiv_id], max_results=1)
    results = list(client.results(search))
    if not results:
        raise LookupError(f"No paper found for arxiv ID '{arxiv_id}'")
    return results[0]


async def _download_pdf(pdf_url: str, dest: Path) -> None:
    """Download a PDF from a URL to dest using httpx (async, non-blocking)."""
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.get(pdf_url)
        response.raise_for_status()
        dest.write_bytes(response.content)


@registry.register(
    name="fetch_arxiv",
    description=(
        "Fetch a paper from arxiv.org by its ID and produce a structured summary "
        "with key claims, methods, results, limitations, and relevance to the active project. "
        "Use this when the user says 'summarize arxiv <ID>', 'fetch arxiv paper <ID>', "
        "'look up arxiv <ID>', or gives any arxiv-style identifier like 2403.12345. "
        "Do NOT call this for local PDFs — use summarize_paper instead. "
        "The ID can be given with or without the 'arXiv:' prefix and with or without a version suffix."
    ),
    parameters={
        "type": "object",
        "properties": {
            "arxiv_id": {
                "type": "string",
                "description": (
                    "The arxiv paper ID, e.g. '2403.12345' or '2403.12345v2' or 'arXiv:2403.12345'."
                ),
            },
        },
        "required": ["arxiv_id"],
    },
)
async def fetch_arxiv(arxiv_id: str) -> dict:
    """Fetch a paper by arxiv ID, download its PDF, and summarize it."""
    # --- Validate and normalize the ID ---
    normalized = _normalize_id(arxiv_id)
    if normalized is None:
        return {
            "error": (
                f"'{arxiv_id}' doesn't look like a valid arxiv ID. "
                "Expected format: 2403.12345 or arXiv:2403.12345v2."
            ),
            "type": "InvalidArxivID",
        }

    # Use the normalized ID (no prefix, no whitespace) as the local filename.
    # Replace '/' in old-format IDs (cond-mat/0411123) with '_' for filesystem safety.
    safe_filename = normalized.replace("/", "_") + ".pdf"
    arxiv_dir = Path("data/arxiv")
    arxiv_dir.mkdir(parents=True, exist_ok=True)
    dest = arxiv_dir / safe_filename

    logger.bind(request_id=f"arxiv-{normalized}").info(
        f"fetch_arxiv: requested '{normalized}' -> '{dest}'"
    )

    # --- Fetch paper metadata from arxiv API ---
    # If we already have the file locally, skip the API call and download entirely.
    if not dest.exists():
        try:
            # _fetch_result_sync is blocking; run_in_executor moves it off the event loop.
            loop = asyncio.get_running_loop()
            result: arxiv.Result = await loop.run_in_executor(
                None, _fetch_result_sync, normalized
            )
        except LookupError as e:
            return {"error": str(e), "type": "PaperNotFound"}
        except Exception as e:
            return {
                "error": f"Couldn't reach the arxiv API: {e}",
                "type": "ArxivAPIError",
            }

        # arxiv 4.0.0 exposes pdf_url as an attribute on Result.
        if not result.pdf_url:
            return {
                "error": f"No PDF available for arxiv ID '{normalized}'.",
                "type": "NoPDFAvailable",
            }

        # --- Download the PDF ---
        try:
            await _download_pdf(result.pdf_url, dest)
            logger.info(f"fetch_arxiv: downloaded '{normalized}' to '{dest}'")
        except httpx.HTTPStatusError as e:
            return {
                "error": f"HTTP {e.response.status_code} when downloading the PDF.",
                "type": "DownloadError",
            }
        except Exception as e:
            return {"error": f"PDF download failed: {e}", "type": "DownloadError"}
    else:
        logger.info(f"fetch_arxiv: '{normalized}' already cached at '{dest}', skipping download")

    # --- Parse the PDF ---
    try:
        parsed = parse_pdf(dest)
    except PDFParseError as e:
        return {"error": f"Couldn't parse the downloaded PDF: {e}", "type": "PDFParseError"}

    if parsed.is_scanned:
        return {
            "error": (
                "The downloaded PDF appears to be scanned — it has no text layer. "
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
    # importance=10: an explicit paper summary is always worth keeping.
    # arxiv_id stored in metadata so future cache checks can match by ID too.
    # Non-fatal: the user still gets the summary even if memory write fails.
    memory_text = (
        f"Paper summary — {summary.title}\n"
        f"Key claims: {'; '.join(summary.key_claims)}\n"
        f"Methods: {summary.methods}\n"
        f"Results: {summary.results}\n"
        f"Limitations: {summary.limitations}\n"
        f"Source: arxiv:{normalized}"
    )
    try:
        project = sqlite_store.get_active_project()
        chroma_id = await vector_store.add(
            memory_text,
            project_id=project["id"],
            metadata={
                "importance": 10,
                "kind": "paper_summary",
                "source_path": str(dest),
                "arxiv_id": normalized,
                "pages": parsed.page_count,
            },
        )
        sqlite_store.save_memory(
            text=memory_text,
            project_id=project["id"],
            importance=10,
            chroma_id=chroma_id,
        )
        logger.info(f"fetch_arxiv: summary saved to project '{project['name']}'")
    except Exception as e:
        logger.warning(f"fetch_arxiv: memory save failed (non-fatal): {e}")

    # Return the full summary dict. The LLM reads key_claims + relevance_to_user aloud.
    result_dict = summary.model_dump()
    result_dict["_meta"] = {
        "arxiv_id": normalized,
        "pages": parsed.page_count,
        "source_path": str(dest),
    }
    return result_dict
