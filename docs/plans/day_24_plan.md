# Day 24 Plan — Arxiv Lookup + PDF Drop UI + Polish

**Week 4, Day 5 — closes the PDF-summarization centerpiece (Days 22–24).**

> **Companion docs:**
> - `docs/Day_by_Day_Plan_v2.md` — original Day 24 scope (arxiv + polish, 6h budget)
> - `docs/project_status/PROJECT_STATUS(DAY_23).md` — what shipped Day 23, what's deferred
> - `.claude/skills/tool-calling-pattern/SKILL.md` — the 4-step pattern for adding tools (Steps 1–4 are mandatory)
> - `.claude/skills/voice-pipeline/SKILL.md` — MUTED re-check rules apply to any tool that takes >2s

---

## Day 24 Goal — single-sentence test

> "I can say 'summarize arxiv 2403.12345' OR drag a PDF onto the blob, and within ~40 seconds Jarvis speaks a structured summary that's also been persisted to project memory."

If both paths work on three real papers from your reading list, Day 24 ships. Polish items (cache, search_dirs expansion) are second-priority.

---

## Pre-flight (T-0) — Do this before any code

### T-0.1 — Gemini quota check (from Day 23 heads-up)

Day 23 burned through `gemini-2.5-flash` and `gemini-flash-lite-latest` quotas. Before T-2:

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

**Decision rule:**
- If any of `gemini-2.5-flash` / `gemini-2.0-flash` is OK → leave `summarizer_model` and `summarizer_chunk_model` as-is from Day 23.
- If both are rate-limited and only `gemini-flash-lite-latest` works → set `summarizer_model = gemini-flash-lite-latest` for today. Structured-output quality drops slightly but is acceptable for arxiv testing. Do NOT block on this.

### T-0.2 — Confirm Day 23 still works

```bash
python -m backend.tests.test_summarize_paper
```

If this fails because of a quota issue, that's expected — just confirm the *code path* runs (you'll see the `_resolve_path` resolution, the parser opening the PDF, and the request leaving for Gemini). If it fails for any other reason, fix that before starting Day 24.

### T-0.3 — Pull and tag

```bash
git pull
git log --oneline -5    # confirm Day 23 commits landed
```

---

## Task list (T-1 through T-7)

| T | Task | Est. time | Carryover? |
|---|---|---|---|
| T-1 | Install `arxiv` and verify version | 15 min | No (planned) |
| T-2 | Build `fetch_arxiv` tool + register | 1.5 h | No (planned) |
| T-3 | Expand `_resolve_path` to include `data/arxiv/` | 15 min | Yes (Day 23 heads-up) |
| T-4 | PyWebView PDF drop wiring (backend + frontend) | 2 h | Yes (deferred from Day 22 + 23) |
| T-5 | Optional: SQLite cache check before running map-reduce | 1 h | Yes (Day 23 heads-up — defer if behind) |
| T-6 | Test on 3 papers from your reading list (1 voice arxiv, 1 PDF drop, 1 voice path) | 45 min | No (planned) |
| T-7 | Verification checklist + journal + commit | 30 min | No |

**Total: ~6 hours** (matches the Day 24 budget in `Day_by_Day_Plan_v2.md`). T-5 is the drop-cut if you're behind.

---

## T-1 — Install and verify `arxiv`

**What:** add the `arxiv` Python package to `backend/requirements.txt` and confirm the install is clean.

**Why:** the `arxiv` package wraps the arxiv.org export API (search, metadata, PDF download). It's a thin client over `urllib` and handles rate-limit politeness internally — much safer than rolling our own HTTP calls against arxiv.

**How:**

```bash
# in repo root, with .venv activated
pip install arxiv
pip freeze | grep -i arxiv
# expected output: arxiv==2.x.x  (note the exact version)
```

Then add the pinned line to `backend/requirements.txt` manually — do NOT do a blanket `pip freeze > requirements.txt` because it'll churn other unrelated versions.

**Acceptance:**
- `python -c "import arxiv; print(arxiv.__version__)"` prints a version
- `backend/requirements.txt` has the new pinned line

**Watch out for:**
- The `arxiv` package was previously `arxiv.py` (different package). Make sure `pip install arxiv` resolves to the maintained one (>2.0.0). If you get something tiny (~0.5.x), you've got the wrong package.

---

## T-2 — Build the `fetch_arxiv` tool

**What:** new file `backend/tools/fetch_arxiv.py`. Registers a tool that takes an arxiv ID, downloads the PDF, and runs it through the same summarization pipeline Day 23 built.

**Why this tool exists at all:** the voice path "summarize this paper at /path/..." works today (Day 23) — but specifying a filesystem path by voice is awkward. Arxiv IDs (`2403.12345`) are spoken cleanly and are unambiguous. This is the natural voice interface for new papers.

### T-2.1 — Decision to make BEFORE coding

**Question for you:** should `fetch_arxiv` be a single tool that fetches AND summarizes in one call, or two separate tools (`fetch_arxiv` returns a path → LLM then calls `summarize_paper`)?

- **Option A — one combined tool** (my recommendation): user says "summarize arxiv 2403.12345" → LLM calls one tool → tool fetches PDF, calls `summarize_paper_text(parsed)`, returns the structured `PaperSummary`. One round-trip with the LLM. The user experience matches what `Day_by_Day_Plan_v2.md` describes: "downloads PDF, runs same pipeline."
- **Option B — two separate tools**: `fetch_arxiv(arxiv_id)` returns a path string. LLM then calls `summarize_paper(path)` in a second tool-call iteration. More composable (e.g. "download this arxiv paper so I can read it later" works) but adds a tool-call iteration per summary request.

**Trade-off:** Option A is simpler, faster, and matches the documented Day 24 deliverable. Option B is more flexible. I'd ship Option A in v1 and add a separate `download_arxiv` tool in Month 2 if you ever want "download without summarizing."

**→ Confirm A or B before I write the tool.** If you don't reply, default is A.

### T-2.2 — Tool implementation (assuming Option A)

Following the 4-step pattern from `tool-calling-pattern/SKILL.md`:

**Step 1 — Create `backend/tools/fetch_arxiv.py`:**

```python
"""Fetch a paper by arxiv ID and run it through the summarization pipeline."""
# This tool is Option A: one tool, fetches AND summarizes.
# It does NOT replace summarize_paper — it complements it for the arxiv path.
# Both tools end up calling summarize_paper_text(parsed) under the hood.

import re
from pathlib import Path

import arxiv  # the maintained package, version 2.x+

from backend.config.settings import get_settings
from backend.pdf.parser import parse_pdf, PDFParseError, ScannedPDF
from backend.services.summarizer import summarize_paper_text, SummarizationError
from backend.tools import registry
# ... memory persistence imports same as summarize_paper.py


# Match arxiv IDs in several common spoken/written forms:
#   2403.12345        (post-2007 format)
#   2403.12345v2      (with version)
#   arXiv:2403.12345  (with prefix)
#   cond-mat/0411123  (pre-2007 format — rare in 2026 but possible)
_ARXIV_ID_RE = re.compile(
    r"^(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+/\d{7})$",
    re.IGNORECASE,
)


@registry.register(
    name="fetch_arxiv",
    description=(
        "Fetch a paper from arxiv.org by its ID and produce a structured summary. "
        "Use this when the user says 'summarize arxiv <ID>', 'fetch arxiv paper <ID>', "
        "or gives any arxiv-style identifier like 2403.12345. "
        "Do not call this for local PDFs — use summarize_paper instead. "
        "The ID can be given with or without the 'arXiv:' prefix and with or without a version suffix."
    ),
    parameters={
        "type": "object",
        "properties": {
            "arxiv_id": {
                "type": "string",
                "description": "The arxiv paper ID, e.g. '2403.12345' or '2403.12345v2' or 'arXiv:2403.12345'.",
            },
        },
        "required": ["arxiv_id"],
    },
)
async def fetch_arxiv(arxiv_id: str) -> dict | str:
    # Implementation: see T-2.3 below
    ...
```

**Step 2 — Description:** the directive ("Do not call this for local PDFs — use summarize_paper instead") is what makes Gemini route the right tool for the right query. Without it, the LLM may call `summarize_paper` with a file path that doesn't exist.

**Step 3 — Lifespan import in `backend/main.py`:**

```python
import backend.tools.fetch_arxiv  # noqa: F401   <- add this line
```

**Step 4 — Smoke test** (manual, via voice or test script).

### T-2.3 — Handler body, in plain English first

Before I write the code, here's what the handler should do step-by-step. Read this and confirm it matches your intent:

1. Normalize the input: strip `arXiv:` prefix if present; strip whitespace.
2. Validate against `_ARXIV_ID_RE`. If no match → soft error `{"error": "...", "type": "InvalidArxivID"}`.
3. Determine download path: `data/arxiv/{normalized_id}.pdf`. Create `data/arxiv/` if missing.
4. **Check if file already exists locally** — if it does, skip the download. This is a cheap win.
5. If not cached locally: use `arxiv.Client()` + `arxiv.Search(id_list=[normalized_id])` → `next(client.results(search))` → `result.download_pdf(dirpath=..., filename=...)`. Wrap in try/except — soft errors for `arxiv.HTTPError`, `StopIteration` (paper not found), network failures.
6. Parse with the existing `parse_pdf(path)` → `ParsedPDF`. Soft errors for `PDFParseError`, `ScannedPDF`.
7. Call `summarize_paper_text(parsed)` → `PaperSummary`. Soft error for `SummarizationError`.
8. Persist to memory (project-scoped, `importance=10`, `kind=paper_summary`, with `source_path` and `arxiv_id` in metadata) — same as `summarize_paper` does.
9. Return the `PaperSummary` as a dict (use `.model_dump()`).

**Watch out for:**
- `arxiv.Client()` is sync; the underlying HTTP calls are blocking. Either wrap the whole download in `run_in_executor`, or use the package's async-friendly bits (check the installed version's docs). **Async is a hard rule in this project** (`CLAUDE.md §8`, `project-architecture/SKILL.md §"Patterns to follow"`) — do not call blocking I/O from the event loop.
- Soft vs hard errors: all errors from this tool should be soft (returned as `{"error": ..., "type": ...}` dicts) — see `tool-calling-pattern/SKILL.md §"Hard vs soft errors"`. The LLM responds gracefully ("I couldn't reach arxiv, try again in a moment") instead of dumping the user into ERROR state for what's a routine network glitch.
- Do NOT call `summarize_paper` (the tool) from inside `fetch_arxiv`. Call `summarize_paper_text(parsed)` (the service function) directly — going through the tool layer would be a tool calling a tool, which the registry isn't designed for.

### T-2.4 — Update `50_tools.md`

Add one line to `backend/prompts/system/50_tools.md` per the `tool-calling-pattern` SKILL gotcha:

> "When the user gives an arxiv ID or says 'arxiv <number>', call `fetch_arxiv`."

Without this, Gemini may not realize it should prefer `fetch_arxiv` over `summarize_paper` for arxiv queries.

**Acceptance for T-2:**
- Backend startup log shows `tools registered: 7` (was 6 after Day 23).
- `python -m backend.tests.test_summarize_paper` still passes (no regression in `summarize_paper`).
- Manual voice test: "Summarize arxiv 2403.12345" (or any valid recent ID) → PDF downloads to `data/arxiv/`, summary spoken.

**Time budget:** 1.5h. Most of it is reading the `arxiv` package's API; the structure is just a clone of Day 23's `summarize_paper.py`.

---

## T-3 — Expand `_resolve_path` to include `data/arxiv/`

**What:** one-line change in `backend/tools/summarize_paper.py`.

**Why:** Day 23's fuzzy resolver only searches `data/test_pdfs/`. Now that `data/arxiv/` exists, a user can say "summarize that arxiv paper about T315I" (referring to one they previously downloaded) and the fuzzy match should find it.

**How:**

```python
# in summarize_paper.py — find the _resolve_path() function
search_dirs = [
    Path("data/test_pdfs"),
    Path("data/arxiv"),       # <- add this line
]
```

**Minimal diff.** Do not refactor the function. Do not add `Path("data")` as a top-level catch-all — that's too broad and would match unrelated PDFs.

**Acceptance:**
- Voice test: "Summarize the gene expression paper" (assuming you have one in `data/test_pdfs/`) → still resolves.
- Voice test after T-2: download an arxiv paper, then in a fresh turn say "summarize the [keyword] paper" → resolves from `data/arxiv/`.

---

## T-4 — PyWebView PDF drop wiring (deferred from Days 22 & 23)

**What:** drag a PDF onto the floating blob window → backend receives the path → triggers `summarize_paper` automatically (or after a voice confirmation, per the decision below).

This is the most uncertain task today because PyWebView's drop support varies across versions and OSes. Allocate the full 2h.

### T-4.1 — Decision to make BEFORE coding

**Question for you:** when a PDF is dropped, what's the trigger?

- **Option A — auto-summarize on drop**: drop → backend immediately runs `summarize_paper(path)` → blob enters THINKING state → speaks summary. Minimal friction. Risk: accidental drops eat ~30s of compute.
- **Option B — drop arms the path, voice confirms**: drop → backend stores the path as "pending PDF" → blob shows a subtle visual cue (e.g. brief tint) → next voice command like "summarize this" picks up the pending path. Safer; matches the documented v1 plan ("Drag PDF on window → 'summarize this' → spoken key claims").

**Trade-off:** `Day_by_Day_Plan_v2.md` Day 22 explicitly described Option B ("'summarize this' → spoken key claims within 15s"). I'd ship Option B because it's what the plan calls for and the safety margin matters.

**→ Confirm A or B.** Default if no reply: B.

### T-4.2 — Backend wiring (assuming Option B)

**Where the path lands:** the cleanest place is a new WebSocket event type `pdf_dropped` with payload `{path: str}`. The orchestrator stores it on `self._pending_pdf_path: Path | None`. When the LLM's next response involves `summarize_paper` and the args contain a phrase like "this paper" / "this PDF" / no explicit path, the tool handler checks `self._pending_pdf_path` first.

Two implementation options here, with different blast radii:

- **Option α — orchestrator-level pending state**: add `self._pending_pdf_path` to `ConversationOrchestrator`. Modify `summarize_paper`'s handler to read it. Couples the tool to the orchestrator (anti-pattern per `voice-pipeline/SKILL.md`).
- **Option β — module-level pending state in a new file**: `backend/services/pending_drop.py` exposes `set_pending_pdf(path)` and `consume_pending_pdf() -> Path | None`. The drop handler sets it; the tool handler consumes it. Decoupled, testable, idiomatic.

Option β is cleaner. Use that.

### T-4.3 — Frontend wiring (assuming Option B)

PyWebView supports drag-and-drop on the underlying webview. Two paths:

1. **HTML5 drop events in React**: standard `onDragOver` + `onDrop` on a div sized to the window. `e.dataTransfer.files[0].path` *should* be available in PyWebView (it's a desktop context, not a sandboxed browser) — but **verify this on your build before relying on it**. If the path isn't there, the file is wrapped in a virtual blob and you can't get its filesystem path without a multipart upload, which is overkill.
2. **PyWebView's `window.events.window_drop_files`** (or similar — verify against the installed pywebview version). This fires Python-side with the dropped file paths, no JS involvement.

**Recommendation:** try (2) first. It's simpler and avoids the path-availability question. Send the path through the existing WebSocket dispatcher with `{"type": "pdf_dropped", "path": "..."}`.

**Visual feedback:** on drop, broadcast a `pdf_pending` state to the React app and have the blob briefly tint / pulse. Don't add a new state to `VoiceState` — this is metadata, not a pipeline state (per `voice-pipeline/SKILL.md`'s rule against new states for orthogonal signals).

### T-4.4 — Watch-outs

- **PyWebView version drift**: drop events have changed shape between 4.x and 5.x. `verify the installed version first` per `CLAUDE.md §4`.
- **Path encoding on Windows**: dropped paths come back as native Windows paths (`C:\Users\...`). `pathlib.Path` handles them, but `_resolve_path`'s fuzzy logic assumes forward-slash paths in some places — sanity-check.
- **Multiple drop**: if the user drops two PDFs at once, the simplest v1 behavior is "last one wins." Document this; don't try to queue.
- **Drop while in any voice state other than IDLE or MUTED**: ignore (or queue), but absolutely do NOT interrupt the in-flight pipeline. The MUTED re-check pattern doesn't help here because drop isn't a voice trigger.

**Acceptance:**
- Drag a PDF onto the floating window → no error in logs.
- Speak "summarize this" within ~30s → tool fires with the dropped path → spoken summary.
- Drag two PDFs → second one is the one summarized.
- Drop with backend in THINKING state → drop is recorded, nothing crashes, summary uses the new path on the next voice turn.

**Time budget:** 2h. If you hit a PyWebView wall after 90 minutes, descope to T-4.1 + T-4.2 only (backend wiring) and defer the frontend drop UI to Day 25 buffer.

---

## T-5 — Optional cache check before map-reduce

**Drop this entirely if you're behind after T-4.** Day 24's centerpiece is arxiv + drop; cache is polish.

**What:** before `summarize_paper_text(parsed)` runs the full map-reduce, check the SQLite `memory` table for a recent (e.g. within 14 days) entry with matching `source_path` and the same project. If found, return the cached summary.

**Why:** the Day 23 status flagged this — if the user asks a follow-up that causes the LLM to re-call `summarize_paper` on the same file, you burn 25–40s and quota for a result that's already in memory.

**How:**

1. Decide where the cache logic lives. **Recommendation: in the `summarize_paper` tool handler, not in `summarizer.py`** — the service function should remain a pure pipeline. The tool layer is the right place to consult prior memory.
2. Confirm the `memory` table schema actually stores `source_path` and `kind`. **Verify in `backend/database/schema.sql` before writing the query.** If it doesn't, this task expands to a schema migration, which is too big for Day 24 — defer.
3. Query: `SELECT content, created_at FROM memory WHERE project_id=? AND kind='paper_summary' AND source_path=? AND created_at > now-14d ORDER BY created_at DESC LIMIT 1`.
4. If hit: deserialize `content` (JSON) back into `PaperSummary`, return it. Log `summarize_paper: cache hit for {path}`.

**Watch out for:**
- Project scoping: the cache lookup must be project-scoped (`project-architecture/SKILL.md §"Patterns to follow"`).
- Same paper across projects: the same PDF summarized under different active projects should produce different cached entries — that's fine, it matches how memory works elsewhere.
- The 14-day TTL is arbitrary; put it in `settings.py` as `paper_summary_cache_days = 14`.

**Acceptance:**
- Summarize a paper → confirm SQLite row appears.
- Re-trigger summarization on the same paper within the same project → log shows `cache hit`, returns in <1s instead of 30s.
- Summarize the same paper under a different active project → re-runs the pipeline (no cross-project leak).

**Time budget:** 1h. Cuttable.

---

## T-6 — Test on 3 papers from your reading list

**What:** the day's substantive verification. Three end-to-end runs across three real papers.

**Why:** Day 23's smoke test ran on `data/test_pdfs/` (two papers). Day 24's whole point is daily-driver value. Three different papers from your actual reading list shake out edge cases that synthetic test data misses.

**Concrete protocol:**

1. **Paper 1 — voice arxiv path:** pick a recent arxiv paper you actually want to read (computational biology preferred — kinase / DTI / protein stability). Say "summarize arxiv {ID}." Listen to the spoken summary end-to-end. Verify the `key_claims` and `relevance_to_user` are coherent.
2. **Paper 2 — drag-drop path:** download a different PDF manually (NOT from arxiv — pick something from a journal or your local file system). Drop on the window, then say "summarize this." Verify drop triggered, summary fires.
3. **Paper 3 — voice fuzzy path:** use a paper already in `data/test_pdfs/` or `data/arxiv/`. Say something natural like "summarize the paper about [topic]." Verify `_resolve_path` fuzzy match resolves it.

For each, log: total latency, whether summary was coherent (1-5 subjective), and any failure modes.

**Acceptance:**
- All three paths produce a spoken summary.
- Subjective coherence ≥3/5 on each (you'd be willing to use this in real work).
- No crashes; any errors are soft (`{"error": ..., "type": ...}`) and the assistant recovers.

**Time budget:** 45 min (~15 min per paper including listening time).

---

## T-7 — Verification, journal, commit

### T-7.1 — Verification checklist (10 items, mirror Day 23 style)

Run these in order:

```
1. python -c "from backend.tools.fetch_arxiv import fetch_arxiv; print('OK')"
   → "OK"

2. Backend startup log:
   → "tools registered: 7"

3. Invalid arxiv ID via voice ("summarize arxiv banana"):
   → {"error": "...invalid arxiv ID...", "type": "InvalidArxivID"} spoken naturally

4. Valid arxiv ID via voice:
   → PDF appears in data/arxiv/, summary spoken, persisted to memory

5. Drag-and-drop PDF onto window:
   → ws event 'pdf_dropped' logged with path

6. After drop, "summarize this":
   → resolves pending path, summary spoken (Option B);
   OR drop → immediate summary (Option A)

7. _resolve_path fuzzy match from data/arxiv/:
   → "summarize the [keyword] paper" resolves correctly

8. Mute mid-summarization (any path):
   → "in-flight task cancelled" + "playback stopped"

9. Memory recall:
   → vector_store.search("[topic from paper 1]") returns the new summary

10. (Optional, only if T-5 shipped) Cache hit:
    → repeat summarization of same paper logs "cache hit", returns <1s
```

### T-7.2 — Update `docs/journal.md`

One line, per the `CLAUDE.md §"Daily Discipline"` rule.

### T-7.3 — Commits (one per logical change)

Expected commit list:
```
[ ] chore(deps): add arxiv package
[ ] feat(tools): fetch_arxiv tool with arxiv ID validation and download
[ ] feat(tools): expand _resolve_path to search data/arxiv/
[ ] feat(desktop): pywebview PDF drop wiring with pending-state service
[ ] (optional) feat(tools): summarize_paper cache check from sqlite memory
[ ] test: 3-paper end-to-end smoke run
[ ] docs: day 24 journal + plan + status
```

---

## Gotchas to watch for (Day 24-specific)

- **arxiv API politeness**: the `arxiv` package handles rate-limiting internally, but if you batch-download 5+ papers in quick succession during testing, you may hit a brief block. Wait a minute and retry.
- **PyWebView drop API surface**: the relevant function name and event shape varies. **Verify against the installed pywebview version** before writing the drop handler (per `CLAUDE.md §4` — version-drift rule).
- **arxiv IDs with version suffix** (`2403.12345v2`): the `arxiv` package usually resolves the latest version when given `2403.12345`. If the user wants a specific version, the suffix should be preserved through normalization. The `_ARXIV_ID_RE` regex above keeps the version; don't strip it.
- **Path string types**: `arxiv.Result.download_pdf` returns a string path on some versions, a `Path` on others. Convert to `Path` immediately after the call.
- **PDF download to a fresh `data/arxiv/`**: confirm the directory exists before download. `Path("data/arxiv").mkdir(parents=True, exist_ok=True)` on first call.
- **Logging request_id**: per `voice-pipeline/SKILL.md`, bind a request_id covering the whole arxiv-fetch + summarization flow. Suggested format: `arxiv-{normalized_id}`.

---

## Drop-cut order (if behind by mid-afternoon)

If T-1 → T-3 are done but T-4 is dragging:

1. **Cut T-5 entirely** (cache is optional polish).
2. **Cut T-4 frontend** — keep T-4 backend (`pdf_dropped` WebSocket event, `pending_drop.py` service) but defer the React drop zone to Day 25 buffer. The backend being ready means tomorrow you only need to wire the UI.
3. **Cut Paper 3 from T-6** — two papers tested is acceptable.
4. **Never cut T-7** — the journal entry and commits are non-negotiable.

If even T-2 is dragging by 2pm: stop, take a walk, descope to "arxiv tool only, no drop UI, no cache." A working arxiv path is the minimum viable Day 24.

---

## Time budget summary

| Block | Time | Cumulative |
|---|---|---|
| T-0 pre-flight | 15 min | 0:15 |
| T-1 arxiv install | 15 min | 0:30 |
| T-2 fetch_arxiv tool | 1.5 h | 2:00 |
| T-3 _resolve_path expansion | 15 min | 2:15 |
| T-4 PDF drop wiring | 2 h | 4:15 |
| T-5 cache check (optional) | 1 h | 5:15 |
| T-6 3-paper testing | 45 min | 6:00 |
| T-7 verification + commit | 30 min | 6:30 |

**Target:** 6h. Within the Day_by_Day_Plan_v2.md budget for Day 24. The optional T-5 brings it to 6.5h, which is the ceiling before fatigue starts costing more than it saves.

---

## What questions need answers before I write any code?

Two decisions, one heads-up:

1. **T-2.1**: `fetch_arxiv` as one combined tool (A) or two separate tools (B)? Default A.
2. **T-4.1**: PDF drop auto-summarizes (A) or arms a pending path for voice confirmation (B)? Default B.
3. **T-0 finding**: which Gemini models are currently usable? Run the snippet at the top, paste the output.

Once those three are settled, I can start on T-1 → T-2 with you.

---

## Closing note

Day 24 closes the three-day centerpiece. By end of today, the voice-first PDF summarization story is complete:

- Local PDF via voice path → Day 23 ✓
- Arxiv ID via voice → Day 24 (T-2)
- Drag-and-drop PDF → Day 24 (T-4)
- Persistent project-scoped memory of summaries → Day 23 + Day 24 cache ✓

This is the resume-worthy week. Tomorrow (Day 25) is web search, which is much smaller scope. If today goes smoothly, you'll have margin to start Day 25 early and use Day 27's wake-word slot for more polish on Days 22–24's centerpiece instead.
