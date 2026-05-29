# Day 23 Plan — Structured Paper Summarization (`summarize_paper` tool)

**Week 4, Day 4 — the centerpiece feature.**
**Date:** 2026-05-30 (planned)
**Time budget:** 6 hours
**Predecessor:** Day 22 — `parse_pdf()` ships, `ParsedPaper` model in place.
**Successor:** Day 24 — arxiv lookup wraps the same pipeline.

> **Day 23 is the day Jarvis stops being a chatbot and starts being a research
> assistant.** Day 22 gave us clean structured text. Today we feed that text to
> Gemini with structured output, hierarchically reduce long papers, register the
> result as a tool the LLM can call by voice, and persist the summary to project
> memory for later recall.

---

## 0. Agenda

By end of day, this voice command works end-to-end:

> *"Summarize the paper at `data/test_pdfs/Biomedical application of protein engineering.pdf`"*

→ pipeline calls `parse_pdf()` → map-reduces 26 chunks → returns a structured
`PaperSummary` → TTS speaks the key claims + relevance → full structure broadcasts
to chat → summary saved as a high-importance memory under the active project.

Five tasks, in order. Each is a logical commit. Stop at task 4 if behind; task 5
(persistence) can slip to Day 24.

| # | Task | Time | What it produces |
|---|---|---|---|
| T-1 | Pre-flight: model availability + Gemini structured-output sanity check | 30 min | Quota cleared; confidence in `gemini-2.5-flash` JSON mode |
| T-2 | `backend/models/summary.py` — `PaperSummary` Pydantic model | 30 min | Schema for structured LLM output |
| T-3 | `backend/services/summarizer.py` — map-reduce pipeline | 2.5 hr | `summarize_paper_text(parsed: ParsedPaper) -> PaperSummary` |
| T-4 | `backend/tools/summarize_paper.py` — registered tool | 1.5 hr | Voice command works end-to-end |
| T-5 | Project-memory persistence of summary | 1 hr | "What did we read about T315I?" recalls the summary |

---

## 1. Pre-flight (30 min, do before anything else)

### 1.1 Gemini model availability check

Day 22's status doc flagged this as critical for today. **Run this first**, before
writing any code:

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
            print(f'NO  {m}: {str(e)[:80]}')
asyncio.run(t())
"
```

**Decision rules** (record outcome in `docs/journal.md`):

- `gemini-2.5-flash` OK → proceed; `gemini_model_heavy` already set correctly.
- `gemini-2.0-flash` also OK → consider switching `gemini_model_heavy` to it
  (saves 2.5-flash quota for Day 25 web-search + Day 26 tools). Suggest, don't
  silently change — flag in journal.
- Both rate-limited → stop. Either wait an hour or enable AI Studio billing
  ($5/month, eliminates problem permanently). Do not proceed on
  `gemini-flash-lite-latest` for JSON-mode summarization — quality will be poor.

### 1.2 Structured-output sanity check

Map-reduce won't work if Gemini won't produce JSON reliably for the chosen model.
Verify with a minimal `response_schema` call before committing to it for chunks:

```bash
python -c "
import asyncio
from google import genai
from google.genai import types
from pydantic import BaseModel
from backend.config.settings import get_settings

class Mini(BaseModel):
    title: str
    one_line_summary: str

async def t():
    s = get_settings()
    client = genai.Client(api_key=s.gemini_api_key)
    r = await client.aio.models.generate_content(
        model='gemini-2.5-flash',
        contents='Title: CRISPR. Summary: gene editing tool.',
        config=types.GenerateContentConfig(
            response_mime_type='application/json',
            response_schema=Mini,
        ),
    )
    print('parsed:', r.parsed)
    print('text:', r.text[:200])
asyncio.run(t())
"
```

Expect: `r.parsed` is a `Mini` instance, `r.text` is valid JSON. If this fails,
investigate before writing T-3.

### 1.3 Pin SDK version

If everything works, `pip freeze | grep google-genai` and check it matches the
already-pinned `2.6.0`. Note any drift in journal.

---

## 2. T-2 — `backend/models/summary.py` (30 min)

### 2.1 Why a dedicated model file

Same reason as `models/pdf.py`: consistency with the codebase (`chat.py`,
`voice.py`, `pdf.py`), free JSON serialisation for tool results, and the schema
will be passed directly to Gemini as `response_schema=PaperSummary` (Pydantic v2
class object — not the dict from `.model_json_schema()`, which would emit `$defs`
that `_validate_schema()` in the tool registry rejects).

### 2.2 The schema (write it yourself, then ask Claude to fill in field docs)

```python
"""Pydantic model for structured paper summaries produced by the summarizer."""

from pydantic import BaseModel, Field


class PaperSummary(BaseModel):
    """Structured summary of a single research paper.

    Fields are ordered so the LLM produces high-signal content first. TTS reads
    `key_claims` + `relevance_to_user`; the rest renders in the chat panel.
    """

    title: str = Field(
        description="The paper's title as best identified from the text."
    )
    key_claims: list[str] = Field(
        description="3-7 bullet points stating the paper's main findings or "
                    "arguments. Each claim is a complete sentence."
    )
    methods: str = Field(
        description="2-4 sentences on the methodology. What did they do?"
    )
    results: str = Field(
        description="2-4 sentences on the findings. What did they observe?"
    )
    limitations: str = Field(
        description="2-3 sentences on caveats, scope, or weaknesses the authors "
                    "or readers should note."
    )
    relevance_to_user: str = Field(
        description="1-2 sentences connecting the paper to the user's active "
                    "research project. If no clear connection, say so plainly."
    )
```

### 2.3 Notes

- Do **not** add `field_validator` constraints (min/max lengths on lists) for v1
  — Gemini occasionally returns 2-bullet `key_claims` for short papers and you
  don't want the whole call to fail validation. Trust the prompt to keep it in
  range; tighten in Day 24 if needed.
- `Field(description=...)` matters: Gemini reads these descriptions when
  generating against `response_schema`. They are not just developer docs.
- Don't import this from `backend/tools/__init__.py` lifespan — it's a model, not
  a tool.

**Commit:** `feat(models): pydantic model for structured paper summaries`

---

## 3. T-3 — `backend/services/summarizer.py` (2.5 hours, the substance)

This is where 80% of the day's complexity lives. Walk this section carefully
before writing code.

### 3.1 Why `services/`, not `tools/`

`pdf_parser.py` is library code in `tools/` because Day 22 organised it there.
The summarizer crosses LLM-router, prompt-loading, and possibly memory — it's
not a single tool implementation, it's the engine behind the tool. `services/`
is where cross-cutting orchestration lives (`conversation.py`, `cost_tracker.py`).

The Day 24 tool module (`backend/tools/summarize_paper.py`) will be a thin
wrapper around `summarizer.summarize_paper_text()`. Same split as Day 11:
orchestrator vs. handler.

### 3.2 Public surface

One async function:

```python
async def summarize_paper_text(parsed: ParsedPaper) -> PaperSummary:
    """Produce a structured summary from a parsed paper.

    Strategy:
    - If paper is scanned: raise SummarizationError (tool layer maps to message).
    - If <= settings.summarizer_direct_threshold chars (~12k): single LLM call
      over the full text with response_schema=PaperSummary.
    - Otherwise: map-reduce. Summarize each chunk to a short intermediate
      summary (string), then synthesize all intermediates into one final
      PaperSummary with response_schema.
    """
```

That's the entire public API. Everything else in the file is private helpers.

### 3.3 Configuration in `settings.py`

Add these to `backend/config/settings.py` so they're tunable without editing
service code (rule #8 from CLAUDE.md — no magic numbers):

```python
# Summarizer
summarizer_direct_threshold: int = 12000   # chars; below this, skip map-reduce
summarizer_chunk_max_concurrent: int = 3   # parallel chunk-summary calls
summarizer_chunk_summary_target: int = 400 # chars; intermediate summary length
summarizer_model: str = "gemini-2.5-flash" # JSON mode needs the better model
```

`summarizer_chunk_max_concurrent=3` is the key safety dial. The 29-page paper
has 22 chunks; firing them all at once would burst the Gemini per-minute rate
limit. 3 in parallel gives ~8 round-trip waves at 1-2s each → roughly 15s total
for the map stage. Acceptable.

### 3.4 Map-reduce structure

```
                    parsed: ParsedPaper
                            │
                ┌───────────┴────────────┐
                │ is_scanned?            │
                │   yes → raise          │
                │   no  → continue       │
                └───────────┬────────────┘
                            │
                ┌───────────┴────────────┐
                │ len(full_text) <= 12k? │
                │   yes → single-pass    │────┐
                │   no  → map-reduce     │    │
                └───────────┬────────────┘    │
                            │                  │
                            ▼                  │
              ┌──────────────────────────┐    │
              │ MAP                      │    │
              │  for chunk in chunks:    │    │
              │    summarize_chunk(...)  │    │
              │  → list[str]             │    │
              │  (bounded concurrency=3) │    │
              └──────────┬───────────────┘    │
                         │                     │
                         ▼                     │
              ┌──────────────────────────┐    │
              │ REDUCE                   │    │
              │  Single Gemini call:     │    │
              │  intermediates → JSON    │◀───┘
              │  schema=PaperSummary     │
              └──────────┬───────────────┘
                         │
                         ▼
                   PaperSummary
```

### 3.5 The two prompts (write to files, do not inline)

Follow the project's existing prompt convention. From Day 21 work, prompts live
in `backend/prompts/`. Create two new files:

#### `backend/prompts/summarizer/chunk_summary.md`

```
You are summarizing one section of a research paper for later synthesis.

Source section: {source_heading_or_none}

Text:
---
{chunk_text}
---

Write a {target_chars}-character summary capturing only:
- Specific factual claims (numbers, gene names, methods, results)
- Limitations or caveats stated in this section
- Anything that would matter for a final overall summary

Do NOT include filler ("This section discusses..."). Open with the claim.
Plain prose, no bullets.
```

#### `backend/prompts/summarizer/final_synthesis.md`

```
You are synthesizing a research paper's structured summary from per-section
notes. The user's active research project is: {active_project_name}.
The user's recent project context: {project_context_short}

Section notes (in order):
---
{joined_intermediates}
---

Produce a structured summary matching the required JSON schema. Rules:

- `title`: use this title if obvious from the notes, otherwise "Unknown title".
- `key_claims`: 3-7 specific claims. Each is a complete sentence. Numbers,
  gene names, drug names — keep them.
- `methods`, `results`, `limitations`: each 2-4 sentences, factual, no hedging
  ("the paper seems to...").
- `relevance_to_user`: tie to the active project explicitly. If no clear
  connection, say "Not directly relevant to {active_project_name}".

Do not invent claims not present in the notes.
```

Load both at service-init time (one file read each) and string-format per call.
Do not re-read on every call.

### 3.6 The map stage — bounded concurrency

This is the one piece of async work today that needs care. Use
`asyncio.Semaphore` to cap concurrent in-flight requests:

```python
import asyncio
from backend.llm import router as llm_router
from backend.config.settings import get_settings


async def _summarize_chunk(chunk: PaperChunk, semaphore: asyncio.Semaphore) -> str:
    """One chunk → one short intermediate summary string."""
    async with semaphore:
        prompt = self._chunk_template.format(
            source_heading_or_none=chunk.source_heading or "(unknown section)",
            chunk_text=chunk.text,
            target_chars=get_settings().summarizer_chunk_summary_target,
        )
        response = await llm_router.generate(
            prompt,
            model=get_settings().summarizer_model,
            # no response_schema — plain text intermediates
        )
        return response.text.strip()


async def _map_stage(chunks: list[PaperChunk]) -> list[str]:
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.summarizer_chunk_max_concurrent)
    tasks = [_summarize_chunk(c, semaphore) for c in chunks]
    # gather preserves order — important for the reduce prompt
    return await asyncio.gather(*tasks)
```

**Two things to think about before writing the code:**

1. **Partial failures.** If chunk 7 of 22 raises (Groq blip, content filter,
   whatever), `asyncio.gather()` by default raises and cancels the rest. For v1
   that's acceptable — surface the error to the user. *Do not* pass
   `return_exceptions=True` and silently drop chunks; that gives a misleading
   "complete" summary based on partial data.
2. **Logging.** Bind a single `request_id` for the whole summarization (e.g.
   `summarize-{path.stem}-{N}`) and pass it to every chunk call so the log
   trail is greppable. Same pattern as the per-turn id discussed in
   `voice-pipeline/SKILL.md`.

### 3.7 The reduce stage — structured output

```python
from google.genai import types
from backend.models.summary import PaperSummary


async def _reduce_stage(intermediates: list[str]) -> PaperSummary:
    active_project = await get_active_project()  # from memory layer
    project_context = await _short_project_context(active_project.id)
    joined = "\n\n---\n\n".join(
        f"[note {i+1}]\n{s}" for i, s in enumerate(intermediates)
    )
    prompt = self._final_template.format(
        active_project_name=active_project.name,
        project_context_short=project_context,
        joined_intermediates=joined,
    )

    # NOT through llm_router because router doesn't currently expose
    # response_schema. Call gemini provider directly OR extend router to pass
    # config through. See §3.8 decision point.
    response = await self._gemini_client.aio.models.generate_content(
        model=get_settings().summarizer_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=PaperSummary,
        ),
    )
    return response.parsed  # already a PaperSummary instance
```

### 3.8 Suggest, don't just write — decision point on `llm_router`

**Stop here and ask before coding.** The router (`backend/llm/router.py`) was
designed in Day 4 for `generate(prompt) -> LLMResponse`. It does not currently
accept `response_schema` or `tools`. Two options:

**Option A — extend `llm_router.generate()`** to accept an optional
`config: types.GenerateContentConfig | None = None` parameter and pass it
through. Pro: all LLM calls funnel through the router; cost tracker keeps
working uniformly. Con: leaks Gemini SDK types into the router interface.

**Option B — direct Gemini client call** in `summarizer.py` only for the reduce
stage (the only place we need `response_schema`). Pro: doesn't pollute the
abstract `BaseProvider` interface. Con: bypasses cost tracking unless we
manually log; OpenAI fallback won't work for the reduce stage.

**Recommendation:** Option A. The router already calls Gemini under the hood;
adding a passthrough config is a small surgical change. OpenAI fallback can stay
unimplemented for reduce — explicit `NotImplementedError` if router falls back
during a structured call. Tool calling in Day 20 already needed similar
passthrough (`tools=[...]`), so the precedent exists.

If undecided after 5 minutes, go Option B for today and refactor on Day 24.
Shipping > purity.

### 3.9 Error type

```python
class SummarizationError(Exception):
    """Raised when summarization cannot complete. Tool layer maps to user msg."""
```

Caller (the tool in T-4) catches this and returns a soft-error string. Don't let
the orchestrator route to ERROR state on a recoverable issue like "this is a
scanned PDF".

**Commit:** `feat(services): map-reduce paper summarizer with structured output`

---

## 4. T-4 — `backend/tools/summarize_paper.py` (1.5 hours)

This is a textbook application of the 4-step pattern in
`.claude/skills/tool-calling-pattern/SKILL.md`. Read that skill first if it's
been a couple of days.

### 4.1 The tool module

```python
"""Voice tool: summarize a research paper from a local PDF path."""

from pathlib import Path
from backend.tools import registry
from backend.tools.pdf_parser import parse_pdf, PDFParseError
from backend.services.summarizer import summarize_paper_text, SummarizationError
from backend.models.summary import PaperSummary


@registry.register(
    name="summarize_paper",
    description=(
        "Read a research paper from a local PDF file and produce a structured "
        "summary with title, key claims, methods, results, limitations, and "
        "relevance to the user's active project. "
        "Use this when the user says 'summarize this paper', 'summarize the "
        "PDF at <path>', or drops a PDF and asks for a summary. "
        "The user must provide the file path; do not guess."
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
    """Return a dict the LLM can summarize verbally + the chat can render."""
    try:
        parsed = parse_pdf(Path(path))
    except FileNotFoundError:
        return {"error": f"No file at {path}.", "type": "FileNotFoundError"}
    except PDFParseError as e:
        return {"error": f"Couldn't open the PDF: {e}", "type": "PDFParseError"}

    if parsed.is_scanned:
        return {
            "error": (
                "That PDF appears to be scanned — no text layer to read. "
                "OCR support is planned for a future version."
            ),
            "type": "ScannedPDF",
        }

    try:
        summary: PaperSummary = await summarize_paper_text(parsed)
    except SummarizationError as e:
        return {"error": str(e), "type": "SummarizationError"}

    # Return as dict — JSON-serialisable per the tool registry contract.
    # Add `pages` and `source_path` for the chat panel context.
    result = summary.model_dump()
    result["_meta"] = {
        "pages": parsed.page_count,
        "source_path": str(parsed.source_path),
    }
    return result
```

### 4.2 System-prompt update

Per `tool-calling-pattern/SKILL.md` §"Update `50_tools.md`": add one line to
`backend/prompts/system/50_tools.md` so the LLM knows when to use the tool:

> When the user asks to summarize a paper or PDF, call `summarize_paper(path=...)`
> and then read back the key_claims and relevance_to_user. Do not summarize from
> memory — always call the tool.

The "always call the tool" wording matches the load-bearing directive that made
`get_current_time` reliable.

### 4.3 Lifespan import

Add to `backend/main.py`:

```python
import backend.tools.summarize_paper  # noqa: F401
```

Confirm startup log shows `tools registered: 6` (was 5 after Day 21).

### 4.4 Smoke test

Run `backend/tests/test_summarize_paper.py` (write it as part of T-4) that
calls the tool directly without the voice loop:

```python
"""Manual smoke test for summarize_paper tool. Run from repo root."""
import asyncio
from pathlib import Path
from backend.tools.summarize_paper import summarize_paper


async def main():
    for pdf in Path("data/test_pdfs").glob("*.pdf"):
        print(f"\n=== {pdf.name} ===")
        result = await summarize_paper(str(pdf))
        if "error" in result:
            print(f"ERROR ({result['type']}): {result['error']}")
            continue
        print(f"Title:    {result['title']}")
        print(f"Pages:    {result['_meta']['pages']}")
        print(f"Claims ({len(result['key_claims'])}):")
        for c in result['key_claims']:
            print(f"  - {c}")
        print(f"Methods:    {result['methods'][:200]}...")
        print(f"Relevance:  {result['relevance_to_user']}")


if __name__ == "__main__":
    asyncio.run(main())
```

Run it. Both Day 22 test PDFs should produce sensible summaries.

### 4.5 Voice smoke test

Restart backend. Via PTT:

> *"Summarize the paper at `data/test_pdfs/Biomedical application of protein engineering.pdf`"*

Expected log trail (per `tool-calling-pattern/SKILL.md`):

```
tool_call iter=0: summarize_paper({'path': '...'})
  → 22 chunk summaries (parallel waves of 3)
  → 1 reduce call (structured output)
tool_result: summarize_paper -> {...full PaperSummary dict...}
```

Then a second LLM iteration where the model reads the result and produces a
spoken response. TTS speaks the key_claims + relevance. Chat panel shows the
full structure.

**Acceptance gate:** 7-page paper finishes in < 20s. 29-page paper finishes
in < 45s. If 29-page paper exceeds 60s, the parallel concurrency cap is too
conservative — bump `summarizer_chunk_max_concurrent` to 5 and retest. If
2.5-flash rate-limits during the run, lower it back to 3 and accept the wait.

**Commit:** `feat(tools): summarize_paper tool with end-to-end voice integration`

---

## 5. T-5 — Project-memory persistence (1 hour, slip if behind)

The Day 30 demo script requires: *"summary recallable later: 'what did we read
about T315I last week?'"*. T-5 makes that work.

### 5.1 What to save

After a successful summary, save to **both** stores:

- **SQLite (`messages` table):** save the assistant's spoken response as a
  normal message turn. Already happens via the existing `_persist_turn` path —
  no new code.
- **ChromaDB:** save a high-importance memory containing the summary structure.
  This is what enables semantic recall.

### 5.2 Where to put the save

Inside `summarize_paper()` in the tool module, after a successful summary and
before returning:

```python
# Persist as high-importance memory under the active project.
# We bypass the LLM importance scorer — a paper summary is always important.
from backend.memory import vector_store
from backend.memory.projects import get_active_project_id

memory_text = (
    f"Paper summary — {summary.title}\n"
    f"Key claims: {'; '.join(summary.key_claims)}\n"
    f"Methods: {summary.methods}\n"
    f"Results: {summary.results}\n"
    f"Limitations: {summary.limitations}\n"
    f"Source: {parsed.source_path.name}"
)
try:
    project_id = await get_active_project_id()
    await vector_store.add(
        memory_text,
        project_id=project_id,
        metadata={
            "kind": "paper_summary",
            "source_path": str(parsed.source_path),
            "pages": parsed.page_count,
        },
    )
except Exception as e:
    # Memory persistence failure is non-fatal — the user still gets the summary.
    logger.warning(f"summary memory save failed: {e}")
```

### 5.3 Recall test

After T-5 ships, this voice exchange should work:

> "Switch to kinase project."
> "Summarize the paper at data/test_pdfs/Biomedical application of protein engineering.pdf"
> [hear summary]
> [next day, or after restart] "What did we read about protein engineering recently?"
> → semantic search retrieves the saved summary → LLM speaks a recap.

Confirm the ChromaDB row landed:

```bash
python -c "
import asyncio
from backend.memory import vector_store
from backend.memory.projects import get_active_project_id
async def t():
    pid = await get_active_project_id()
    hits = await vector_store.search('protein engineering', project_id=pid, k=3)
    for h in hits:
        print(h)
asyncio.run(t())
"
```

**Commit:** `feat(memory): persist paper summaries as high-importance memories`

---

## 6. Verification checklist (run before declaring Day 23 done)

```
1. Imports clean:
   python -c "from backend.tools.summarize_paper import summarize_paper; print('OK')"
   → "OK"

2. Direct tool smoke test:
   python -m backend.tests.test_summarize_paper
   → Both test PDFs produce summaries with non-empty key_claims, no exceptions

3. Scanned PDF graceful degradation:
   (use the blank-page PDF from Day 22)
   → returns {"error": "...scanned...", "type": "ScannedPDF"}, no crash

4. Bad path:
   → returns {"error": "No file at...", "type": "FileNotFoundError"}

5. Voice end-to-end:
   "Summarize the paper at data/test_pdfs/<short_paper>.pdf"
   → tool fires, structured summary spoken (key_claims + relevance), full
     structure visible in chat panel

6. Latency targets:
   - 7-page paper: < 20s end-to-end
   - 29-page paper: < 45s end-to-end

7. Mute during summarization:
   - Start a 29-page summary, hit Ctrl+Alt+J mid-flight
   - Pipeline halts at next LLM iteration boundary (within ~3s)
   - No exceptions thrown; state goes to MUTED

8. Memory persistence:
   - After a successful summary, run the recall test from §5.3
   - Returns the summary in semantic search

9. Tools count:
   - Backend startup log: "tools registered: 6"

10. No schema validation errors:
    - Run startup, no ToolSchemaError raised by registry validation
```

---

## 7. Heads-up for Day 24 and beyond

### Day 24 (arxiv + polish) inherits

- The `summarize_paper_text(parsed)` function. Day 24's `fetch_arxiv` tool
  downloads a PDF, calls `parse_pdf`, then calls `summarize_paper_text`. Reuse,
  don't reimplement.
- The chunking + map-reduce strategy. Don't tune `summarizer_direct_threshold`
  or `summarizer_chunk_max_concurrent` on Day 23 — let Day 24 do that after
  real usage on 3+ papers.
- The "drop a PDF on the window" UX. Punted from Day 22 to Day 24. The tool is
  ready to receive a path from any source; Day 24 only needs to wire the
  PyWebView drop handler to send the path via WebSocket.

### Known limitations to document in the Day 23 status doc

- Title heuristic still loses on HHS Public Access watermarks (Day 22 carryover).
  The LLM may say "Unknown title" or "HHS Public Access" for those papers —
  acceptable; mention in the demo script.
- Map-reduce gives a coherent summary but not a perfectly faithful one.
  Information from a chunk that doesn't make it into the intermediate string is
  lost forever in the reduce stage. v1 acceptable; v2 could keep a "raw quotes"
  list in chunk summaries.
- Reduce-stage `relevance_to_user` quality depends on `active_project_name`
  being meaningful. The default "general" project gives weak relevance. Strongly
  suggest creating a real project before summarizing — note in README.

### Gemini quota watch

Days 22 and 23 both burned through Gemini calls. Day 25 (web search) and Day 26
(timers) are lighter. Day 27 wake word is local. Quota should hold, but check
the dashboard before Day 25 and consider enabling billing if you've hit limits
twice.

### Don't refactor `pdf_parser.py`

Day 22 noted ~240 lines, within the 300-line soft cap. Day 23 may surface
desires to split it. Resist — Day 24 will know whether the split is needed once
arxiv flow is in. Premature splitting = noise diff.

---

## 8. Drop-cut order (if behind by 3 PM)

| Cut # | What to cut | What still works |
|---|---|---|
| 1 | T-5 (memory persistence) | Tool works, just no semantic recall of past summaries |
| 2 | T-4 voice integration; keep direct smoke test only | Function callable from Python; tomorrow wire to voice |
| 3 | Map-reduce; only support papers below the 12k-char threshold | 7-page paper works; 29-page paper returns "too long" |

Never cut: T-2 (model), T-3 map stage (the engine), basic tool registration
without persistence. Those are the substance of the day. The rest is polish.

---

## 9. End-of-day journal entry template

```
## Day 23 — Structured paper summarization

**Status:** [Complete | Partial | Cut]

**Shipped:**
- PaperSummary Pydantic model
- summarize_paper_text() in services/summarizer.py with map-reduce
- summarize_paper tool registered (tools count: 6)
- [+ T-5 if done] Project-memory persistence

**Measurements (real numbers):**
- 7-page paper: __s end-to-end
- 29-page paper: __s end-to-end
- Chunk parallelism setting that worked: __
- Gemini model used for summarizer: __

**Decisions:**
- llm_router option chosen: [A passthrough | B direct]
- Other:

**Known issues for Day 24:**
-

**Quota status:** Gemini __, Groq __
```

---

## 10. References (read if stuck)

- `.claude/skills/voice-pipeline/SKILL.md` — §"Adding tool calls inside THINKING (Day 20)"
  for the MUTED re-check rule the orchestrator already implements.
- `.claude/skills/tool-calling-pattern/SKILL.md` — §"The 4-step pattern" and
  §"Hard vs soft errors". This is the playbook for T-4.
- `.claude/skills/project-architecture/SKILL.md` — §"Project-scoped everything"
  for the memory write in T-5.
- Day 22 status doc — §4 "Heads-up for Day 23". Specifically the Gemini
  pre-flight, the large average chunk size (3247 chars), and the
  `source_heading=None` fallback signal.
- Gemini structured output docs: https://ai.google.dev/gemini-api/docs/structured-output
  (pin a tab; you'll reference this 4 times today).
