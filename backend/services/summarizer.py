"""Paper summarization service using map-reduce over parsed chunks.

Public entry point:
    summarize_paper_text(parsed: ParsedPaper) -> PaperSummary

Strategy:
- Scanned PDFs → raise SummarizationError (tool layer maps to user message).
- Short papers (full_text <= summarizer_direct_threshold chars):
    one structured Gemini call over the full text; response_schema=PaperSummary.
- Long papers:
    MAP  — summarize each chunk to a short intermediate string (parallel, bounded).
    REDUCE — one structured Gemini call over all intermediates; response_schema=PaperSummary.
"""

import asyncio
import uuid
from pathlib import Path

from loguru import logger

from backend.config.settings import get_settings
from backend.llm.router import get_router
from backend.memory.sqlite_store import get_active_project
from backend.models.pdf import ParsedPaper, PaperChunk
from backend.models.summary import PaperSummary


class SummarizationError(Exception):
    """Raised when summarization cannot complete. Tool layer maps to user message."""


# Prompt templates are loaded once at first use (module import) and reused for
# every call — avoids a file-read on each summarization request.
_PROMPTS_DIR = Path("backend/prompts/summarizer")


class _PaperSummarizer:
    """Owns the prompt templates and orchestrates map-reduce."""

    def __init__(self) -> None:
        self._chunk_template = (_PROMPTS_DIR / "chunk_summary.md").read_text(encoding="utf-8")
        self._final_template = (_PROMPTS_DIR / "final_synthesis.md").read_text(encoding="utf-8")

    async def summarize(self, parsed: ParsedPaper) -> PaperSummary:
        if parsed.is_scanned:
            raise SummarizationError(
                "That PDF appears to be scanned — no text layer to read. "
                "OCR support is planned for a future version."
            )

        settings = get_settings()
        # Bind a unique id to every log line for this summarization run.
        request_id = f"summarize-{Path(parsed.source_path).stem[:20]}-{uuid.uuid4().hex[:6]}"
        logger.info(
            f"[{request_id}] starting: {len(parsed.full_text)} chars, "
            f"{len(parsed.chunks)} chunks, {parsed.page_count} pages"
        )

        if len(parsed.full_text) <= settings.summarizer_direct_threshold:
            return await self._single_pass(parsed, request_id)
        return await self._map_reduce(parsed, request_id)

    # ------------------------------------------------------------------
    # Single-pass path (short papers)
    # ------------------------------------------------------------------

    async def _single_pass(self, parsed: ParsedPaper, request_id: str) -> PaperSummary:
        """One structured Gemini call over the full text — used for short papers."""
        active_project = get_active_project()
        settings = get_settings()

        # Use the final-synthesis prompt but pass the full text as the "notes".
        prompt = self._final_template.format(
            active_project_name=active_project["name"],
            project_context_short="(full paper text — no section notes)",
            joined_intermediates=parsed.full_text[: settings.summarizer_direct_threshold],
        )
        logger.info(f"[{request_id}] single-pass (text <= {settings.summarizer_direct_threshold} chars)")

        router = get_router()
        response = await router.generate(
            prompt,
            model=settings.summarizer_model,
            response_schema=PaperSummary,
        )
        # response.parsed is a PaperSummary instance because response_schema was set.
        return response.parsed

    # ------------------------------------------------------------------
    # Map-reduce path (long papers)
    # ------------------------------------------------------------------

    async def _map_reduce(self, parsed: ParsedPaper, request_id: str) -> PaperSummary:
        logger.info(f"[{request_id}] map stage: {len(parsed.chunks)} chunks")
        intermediates = await self._map_stage(parsed.chunks, request_id)
        logger.info(f"[{request_id}] reduce stage: {len(intermediates)} intermediates")
        return await self._reduce_stage(intermediates, request_id)

    async def _map_stage(self, chunks: list[PaperChunk], request_id: str) -> list[str]:
        # asyncio.Semaphore caps in-flight calls to prevent rate-limit bursts.
        # gather() preserves order — the reduce prompt needs notes in page order.
        semaphore = asyncio.Semaphore(get_settings().summarizer_chunk_max_concurrent)
        tasks = [self._summarize_chunk(chunk, semaphore, request_id) for chunk in chunks]
        return await asyncio.gather(*tasks)

    async def _summarize_chunk(
        self, chunk: PaperChunk, semaphore: asyncio.Semaphore, request_id: str
    ) -> str:
        settings = get_settings()
        async with semaphore:
            prompt = self._chunk_template.format(
                source_heading_or_none=chunk.source_heading or "(unknown section)",
                chunk_text=chunk.text,
                target_chars=settings.summarizer_chunk_summary_target,
            )
            logger.debug(
                f"[{request_id}] chunk {chunk.chunk_index}: "
                f"{chunk.char_count} chars, heading={chunk.source_heading!r}"
            )
            router = get_router()
            # Use the lite model for chunk summaries — plain text, high RPM limit.
            # The heavy model (summarizer_model) is reserved for the reduce stage
            # where JSON-mode structured output actually needs the quality.
            response = await router.generate(prompt, model=settings.summarizer_chunk_model)
            return response.text.strip()

    async def _reduce_stage(self, intermediates: list[str], request_id: str) -> PaperSummary:
        active_project = get_active_project()
        settings = get_settings()

        # Join with numbered dividers so the model can see section boundaries.
        joined = "\n\n---\n\n".join(
            f"[note {i + 1}]\n{s}" for i, s in enumerate(intermediates)
        )
        prompt = self._final_template.format(
            active_project_name=active_project["name"],
            project_context_short="(synthesized from per-section notes)",
            joined_intermediates=joined,
        )

        router = get_router()
        response = await router.generate(
            prompt,
            model=settings.summarizer_model,
            response_schema=PaperSummary,
        )
        # response.parsed is a validated PaperSummary instance.
        return response.parsed


# ---------------------------------------------------------------------------
# Module-level singleton + public entry point
# ---------------------------------------------------------------------------

# Instantiated on first call — reads prompt files once, reuses forever.
_summarizer: _PaperSummarizer | None = None


def _get_summarizer() -> _PaperSummarizer:
    global _summarizer
    if _summarizer is None:
        _summarizer = _PaperSummarizer()
    return _summarizer


async def summarize_paper_text(parsed: ParsedPaper) -> PaperSummary:
    """Public entry point: parse a PDF and produce a structured summary.

    Raises SummarizationError for scanned PDFs or when the LLM call fails.
    """
    return await _get_summarizer().summarize(parsed)
