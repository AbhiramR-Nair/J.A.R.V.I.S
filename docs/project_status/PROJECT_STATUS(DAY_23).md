# Project Status — Day 23

**Period covered:** Day 23 (Week 4, Day 4 — Structured Paper Summarization)
**Status:** Complete — all 5 tasks shipped, all 10 verification checklist items confirmed.
**Environment:** Windows 11, Python 3.13.5, google-genai==2.6.0, pymupdf==1.27.2.3

> Checkpoint summary for Day 23: the centerpiece feature. Jarvis can now summarize
> a research paper from a PDF via voice, producing a structured summary with key claims,
> methods, results, limitations, and relevance to the active project — all spoken back
> and persisted to memory. Read before Day 24.

---

## 1. What has been done

| Task | What landed | Status |
|---|---|---|
| T-1 — Pre-flight | `gemini-2.5-flash` OK; `gemini-2.0-flash` still 429. Structured-output sanity check passed (`r.parsed` returns live Pydantic instance). SDK confirmed at `google-genai==2.6.0`. | Done |
| T-2 — `backend/models/summary.py` | `PaperSummary` Pydantic v2 model: 6 fields (`title`, `key_claims`, `methods`, `results`, `limitations`, `relevance_to_user`). `Field(description=...)` on every field — these are read by Gemini as prompt instructions during JSON-mode generation. | Done |
| T-3 — LLM layer extension (Option A) | `model` and `response_schema` params added to `BaseProvider.generate()`, `GeminiProvider.generate()`, `GroqLLMProvider.generate()`, `LLMRouter.generate()`. `TextResponse.parsed` field added (populated when `response_schema` is set). Router skips fallback for structured calls (Groq can't do JSON mode). | Done |
| T-3 — `backend/config/settings.py` | 5 new settings: `summarizer_direct_threshold` (12000 chars), `summarizer_chunk_max_concurrent` (3), `summarizer_chunk_summary_target` (400 chars), `summarizer_model` (`gemini-2.5-flash`), `summarizer_chunk_model` (`gemini-flash-lite-latest`). | Done |
| T-3 — Prompt files | `backend/prompts/summarizer/chunk_summary.md` (intermediate map prompt) and `final_synthesis.md` (structured reduce prompt). Loaded once at service init via `_PaperSummarizer.__init__`, not on each call. | Done |
| T-3 — `backend/services/summarizer.py` | `SummarizationError` exception. `_PaperSummarizer` class with `summarize()` dispatcher, `_single_pass()` (short papers ≤12k chars), `_map_reduce()` → `_map_stage()` (asyncio.Semaphore bounded concurrency) + `_reduce_stage()` (structured output). Module-level singleton. Public entry point: `summarize_paper_text(parsed) -> PaperSummary`. | Done |
| T-4 — `backend/tools/summarize_paper.py` | `@registry.register` handler with `_resolve_path()` fuzzy helper (see §3). All 4 error paths return soft dicts (FileNotFoundError, PDFParseError, ScannedPDF, SummarizationError). Lifespan import added to `main.py`. `50_tools.md` updated with summarize directive. | Done |
| T-4 — `backend/tests/test_summarize_paper.py` | Manual smoke test: iterates all PDFs in `data/test_pdfs/`, prints full structured output per paper, tests bad-path and corrupt-file error paths. Rate-limit-aware delay (45s) between papers. | Done |
| T-5 — Memory persistence | Summary written to both ChromaDB and SQLite after every successful call. Hard-coded `importance=10` (explicit paper summary always worth keeping — bypasses LLM scorer). Non-fatal: memory failure logs a warning but still returns the summary. | Done |

**Verification checklist (§6 of day_23_plan.md):** All 10 items confirmed.

- Items 1–4: programmatic (import, smoke test, scanned PDF, bad path)
- Item 5: voice end-to-end confirmed live — PTT → STT → tool_call → map-reduce → TTS spoke summary
- Item 6: 7-page ~25s, 29-page ~40s (both within targets)
- Item 7: mute mid-summarization confirmed — "playback stopped, in-flight task cancelled"
- Item 8: ChromaDB recall confirmed (3 hits for "protein engineering" query)
- Items 9–10: `tools registered: 6`, no ToolSchemaError

---

## 2. Implementation strategy — the *why* behind non-obvious choices

### Decision A — Option A for `llm_router` extension (not Option B direct client)

The plan offered two choices for how the reduce stage calls Gemini with `response_schema`:

- **Option A:** extend `LLMRouter.generate()` to accept `model` and `response_schema`, thread through to `GeminiProvider`. Cost tracker keeps working; all LLM calls funnel through one path.
- **Option B:** direct `genai.Client` call in `summarizer.py` only. Simpler for the reduce stage, but bypasses cost tracking and adds a second Gemini client construction.

Chose **Option A**. Changes were surgical: 3 new parameters across 4 files, `TextResponse.parsed: Any = None` added. The router correctly raises `LLMError` (no fallback) when `response_schema` is set and Gemini is unavailable — Groq cannot produce structured JSON output.

`GeminiProvider.generate()` also accepts a `model` override per call. This allows the summarizer to use `gemini-flash-lite-latest` for cheap chunk summaries and `gemini-2.5-flash` for the structured reduce call — without two separate provider instances.

### Decision B — Two-model split for map vs. reduce stages

Original plan had `summarizer_model = gemini-2.5-flash` for all summarizer calls. **Problem:** `gemini-2.5-flash` has a 5 RPM free-tier limit. A 26-chunk paper fires ~9 waves of 3 calls each — the rate limit is hit within seconds.

**Fix:** introduced `summarizer_chunk_model = gemini-flash-lite-latest` (15 RPM free tier) for the map stage (plain text intermediates — quality doesn't require the heavy model). `summarizer_model = gemini-2.5-flash` is reserved for the reduce and single-pass stages where JSON-mode structured output actually needs the better model.

This means chunk summaries can also fall back to Groq (which handles them correctly as plain text), while the reduce stage fails fast if Gemini is unavailable rather than silently returning unstructured text.

### Decision C — `_resolve_path()` fuzzy helper in the tool handler

STT renders spoken file paths as natural speech: "data test PDF gene expression in mammalian cells and its applications." The LLM then constructs a path like `data/test/PDF/gene expression...` which doesn't match the actual filesystem.

**Fix:** `_resolve_path()` sits before the `@registry.register` decorator in `summarize_paper.py`. It:
1. Tries the exact path (covers properly-quoted paths).
2. Scores every PDF in `data/test_pdfs/` by how many words from the spoken input appear in the filename.
3. Returns the best match if ≥2 words hit.
4. Falls through to `FileNotFoundError` for genuinely unknown papers.

**Important:** `_resolve_path` must be defined **before** the `@registry.register(...)` decorator block. If placed between the decorator and `async def summarize_paper`, Python applies the decorator to the helper function instead (see §3 — Bug A).

### Decision D — Prompts loaded once, reused forever

Both `chunk_summary.md` and `final_synthesis.md` are read in `_PaperSummarizer.__init__()` (on first call), not per-summarization-call. The singleton `_get_summarizer()` pattern means the file reads happen once per backend process lifetime. Consistent with how `loader.py` handles system prompts.

### Decision E — `asyncio.gather` raises on partial map failure (no `return_exceptions`)

If any chunk call fails (network blip, content filter, quota exhaustion), the entire map stage fails loudly. The alternative (`return_exceptions=True`) would silently drop failed chunks and produce a misleading "complete" summary from partial data. Loud failure is correct for v1.

---

## 3. Problems faced and how they were handled

### Problem A — `@registry.register` applied to `_resolve_path` helper (decorator placement bug)

**Symptom:** `ToolError: tool 'summarize_paper' handler must be an async function (got <function _resolve_path at ...>)` on import.

**Root cause:** The `_resolve_path` helper was inserted via `Edit` between the `@registry.register(...)` decorator call and `async def summarize_paper`. In Python, a decorator applies to the **next** function definition — so the registry received `_resolve_path` (sync), not `summarize_paper` (async).

**Fix:** moved `_resolve_path` above the decorator block. The file now reads: helper function → decorator → async handler. The registry correctly receives `summarize_paper`.

**Lesson:** when inserting a helper between a decorator and its target, always check that the decorator still sits immediately above `async def`.

### Problem B — `gemini-2.5-flash` 5 RPM limit exhausted during map stage

**Symptom:** first smoke test run failed mid-map-stage with `429 RESOURCE_EXHAUSTED, limit: 5, model: gemini-2.5-flash`. Groq fallback handled some chunks but also hit its 12k TPM limit under the volume.

**Root cause:** 26 chunks × concurrency=3 fires calls faster than `gemini-2.5-flash`'s 5 RPM free-tier cap.

**Fix:** introduced `summarizer_chunk_model = gemini-flash-lite-latest` (15 RPM) for the map stage. The heavy model is now only called once (reduce stage). In normal voice usage this isn't an issue since papers are summarized one at a time.

### Problem C — Transient 503 on reduce stage after heavy map stage

**Symptom:** map stage completed successfully, but the immediately following reduce call returned `503 UNAVAILABLE: This model is currently experiencing high demand.`

**Root cause:** burst of map-stage calls pushed the model into a short "high demand" window. The router correctly blocked the Groq fallback (structured output not supported) and raised `LLMError`.

**Fix:** none needed in code — waiting ~15s and retrying succeeded. This is a transient condition, not a quota issue. In voice usage there is a natural ~5s gap between the map stage completing and the user's next request, so this is unlikely to occur.

### Problem D — Voice path resolution failing on first attempt

**Symptom:** user said "summarize the PDF at data test PDF gene expression in mammalian cells and its applications." STT rendered it faithfully; LLM constructed path `data/test/PDF/gene expression in mammalian cells and its applications.pdf` — not a valid filesystem path. Tool returned `FileNotFoundError`.

**Root cause:** STT has no knowledge of the filesystem. Spoken path fragments don't match directory names (`test_pdfs` vs "test PDF") and filenames contain mixed case.

**Fix:** `_resolve_path()` fuzzy helper (Decision C above). After the fix, the identical voice input correctly resolved to the actual file.

---

## 4. Heads-up for Day 24 and beyond

### Check Gemini model availability before Day 24 — important

Day 23 burned through quota on both `gemini-2.5-flash` (reduce stage) and `gemini-flash-lite-latest` (chunk summaries). Before starting Day 24:

```bash
python -c "
import asyncio
from google import genai
from backend.config.settings import get_settings
s = get_settings()
client = genai.Client(api_key=s.gemini_api_key)
async def t():
    for m in ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-flash-lite-latest']:
        try:
            r = await client.aio.models.generate_content(model=m, contents='ping')
            print(f'OK  {m}')
        except Exception as e:
            print(f'NO  {m}: {str(e)[:60]}')
asyncio.run(t())
"
```

**Decision rules:**

- If `gemini-2.0-flash` OR `gemini-2.5-flash` is available:
  - Set `gemini_model_heavy = gemini-2.0-flash` (or `gemini-2.5-flash`) in `settings.py`
  - Set `summarizer_model = gemini-2.0-flash` (or `gemini-2.5-flash`) in `settings.py`
  - These have the same JSON-mode quality with a higher RPM than flash-lite
- If both are rate-limited:
  - Proceed with `gemini-flash-lite-latest` for both `gemini_model` and `summarizer_model`
  - Structured output quality will be slightly lower but still acceptable for Day 24's arxiv work
  - **Do not block Day 24 on quota** — the feature work doesn't depend on a specific model tier

### `_resolve_path` only searches `data/test_pdfs/`

The fuzzy resolver currently scores PDFs only in `data/test_pdfs/`. Day 24's `fetch_arxiv` tool will download papers to `data/arxiv/` (or similar). When that directory is added, update the `search_dirs` list in `_resolve_path`:

```python
search_dirs = [Path("data/test_pdfs"), Path("data/arxiv"), Path("data")]
```

Do this as part of the arxiv tool, not before.

### File drop UI still deferred

Drag-and-drop PDF onto the blob was deferred from Day 22 and again from Day 23. Day 24 is the planned day for this. The tool already accepts any path — Day 24 only needs to wire the PyWebView drag handler to send the resolved path via a new WebSocket event or `POST /pdf/dropped` endpoint. The backend is ready.

### Map-reduce is called on every `summarize_paper` invocation — no caching

If the user asks to summarize the same paper twice (e.g., a follow-up question causes the LLM to re-call the tool), the full map-reduce runs again. This burns quota and takes ~25-40 seconds.

**Day 24 mitigation (if it comes up):** check SQLite `memory` table for a recent `kind=paper_summary` entry matching `source_path` before running the pipeline, and return the cached summary if it's less than N days old. Not critical for Day 24 — only if the user runs into it.

### Groq TPM limit under back-to-back stress tests

When running multiple papers consecutively (smoke tests, benchmarks), Groq's 12k TPM limit hits after ~1-2 papers. This does not affect normal voice usage but makes automated batch testing unreliable without 45s+ waits between runs. The smoke test already includes a 45s delay.

---

## 5. How to verify Day 23

All already confirmed. For completeness:

```
1. python -c "from backend.tools.summarize_paper import summarize_paper; print('OK')"
   → "OK"

2. python -m backend.tests.test_summarize_paper
   → Both PDFs: non-empty key_claims, no exceptions (allow 45s wait between papers)

3. Scanned PDF (blank page via fitz):
   → {"error": "...scanned...", "type": "ScannedPDF"}

4. Bad path:
   → {"error": "No file found at...", "type": "FileNotFoundError"}

5. Voice: "Summarize the PDF at [paper name spoken]"
   → fuzzy resolved, tool fires, TTS speaks key_claims + relevance ✓

6. Latency: 7-page ~25s, 29-page ~40s ✓

7. Mute mid-summarization:
   → "playback stopped (muted while speaking)" + "in-flight task cancelled" ✓

8. Memory recall:
   → vector_store.search("protein engineering") returns paper summary hits ✓

9. tools registered: 6 ✓

10. No ToolSchemaError on startup ✓
```

---

## 6. Files changed this day

```
NEW:
  backend/models/summary.py                   -- PaperSummary Pydantic model
  backend/services/summarizer.py              -- map-reduce pipeline + SummarizationError
  backend/prompts/summarizer/chunk_summary.md -- map stage prompt
  backend/prompts/summarizer/final_synthesis.md -- reduce stage prompt
  backend/tools/summarize_paper.py            -- registered tool + _resolve_path fuzzy helper
  backend/tests/test_summarize_paper.py       -- smoke test for both PDFs + error paths
  docs/project_status/PROJECT_STATUS(DAY_23).md -- this file
  docs/plans/day_23_plan.md                   -- day plan (committed for record)

EDIT:
  backend/llm/base.py          -- TextResponse.parsed field; model + response_schema in abstract
  backend/llm/gemini.py        -- model override, response_schema config, TextResponse.parsed
  backend/llm/groq_llm.py      -- accept new params; raise if response_schema provided
  backend/llm/router.py        -- thread model + response_schema; skip fallback on structured calls
  backend/config/settings.py   -- 5 summarizer settings added
  backend/main.py              -- lifespan import for summarize_paper tool
  backend/prompts/system/50_tools.md -- summarize_paper directive added
  docs/journal.md              -- Day 23 entry
```

---

## 7. Commits

```
[ ] feat(models): pydantic model for structured paper summaries
[ ] feat(services): map-reduce paper summarizer with structured output
[ ] feat(tools): summarize_paper tool with end-to-end voice integration
[ ] feat(memory): persist paper summaries as high-importance memories
[ ] docs: day 23 plan, project status, and journal entry
```
