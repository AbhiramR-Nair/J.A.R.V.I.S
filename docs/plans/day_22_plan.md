# Day 22 Plan — PDF Parsing (Foundation for Summarization)

**Week 4, Day 3 of 11 — first day of the PDF + Arxiv summarization centerpiece (Days 22–24).**

**Scope today:** parsing only. No LLM summarization, no tool call, no voice integration. Build the foundation: drop a PDF on the window → backend receives the path → `parse_pdf(path)` returns structured sections + chunks. Day 23 wires this into an LLM-powered `summarize_paper` tool. Day 24 adds arxiv lookup and polish.

**Why split this way:** PDF parsing has many failure modes (scanned PDFs, encrypted PDFs, weird layouts, multi-column papers, no text layer). Getting parsing solid before summarization means Day 23's LLM work isn't fighting parser bugs. The Day-by-Day Plan v2 budgets 6 hours for this — most of it is edge-case handling.

---

## 0. Pre-flight (15 min) — do these before writing any code

### 0.1 Gemini quota check

The Day 21 status doc flagged this as critical. Day 22 does NOT make many LLM calls (no summarization yet), so you can defer the model switch to tomorrow if quota is tight today. But still check — if `gemini-2.5-flash` or `gemini-2.0-flash` is available, switch now so smoke tests work normally:

```bash
python -c "
import asyncio
from google import genai
from backend.config.settings import get_settings
s = get_settings()
client = genai.Client(api_key=s.gemini_api_key)
async def t():
    for m in ['gemini-2.0-flash', 'gemini-2.5-flash']:
        try:
            r = await client.aio.models.generate_content(model=m, contents='ping')
            print(f'OK  {m}')
        except Exception as e:
            print(f'NO  {m}: {str(e)[:60]}')
asyncio.run(t())
"
```

If `gemini-2.0-flash` is available → update `settings.py` (or set `GEMINI_MODEL` in `.env`). If both blocked → stay on `gemini-flash-lite-latest` for today, revisit before Day 23.

### 0.2 Install pymupdf

```bash
pip install pymupdf
pip freeze | grep -i pymupdf >> backend/requirements.txt
```

Verify the install:
```bash
python -c "import fitz; print(fitz.__version__)"
```

**Note:** the import is `fitz`, not `pymupdf`. This is a long-standing naming quirk. Don't fight it.

### 0.3 Pick test PDFs

You need at least **3 PDFs in `data/test_pdfs/`** (gitignored — add to `.gitignore` if not already excluded). Choose deliberately:

1. **A "clean" arxiv paper** — 10–20 pages, well-structured (Abstract, Introduction, Methods, Results, Discussion sections). This is your happy path. Example: any recent ML or bioinformatics paper on arxiv.
2. **A long paper** — 40+ pages with many sections. Tests chunking strategy.
3. **A scanned PDF** — no text layer (you can scan a page on your phone, or find one online). Tests the graceful-fail path. If you can't find one, mock it in Step 5 by creating an empty-text PDF.

Optional 4th: a paper with two-column layout (most scientific papers). Tests that pymupdf's text extraction handles columns correctly.

### 0.4 Read the relevant project files (5 min, do not skip)

- `backend/tools/registry.py` — you won't add a tool today, but you'll reference the pattern tomorrow
- `backend/tools/log_to_project.py` — closest existing example of a tool that writes data; mentally template how you'd structure `summarize_paper` for Day 23
- `.claude/skills/tool-calling-pattern/SKILL.md` — refresh on the 4-step pattern (relevant tomorrow)
- `.claude/skills/voice-pipeline/SKILL.md` §"Adding tool calls inside THINKING (Day 20)" — the MUTED re-check rule will matter for the long summarization tool tomorrow

### 0.5 Record the v2-deferral decision in `docs/journal.md`

Before writing any code, add a one-liner to `docs/journal.md` so future-you (or anyone reading the repo) knows why the parser is text-only:

```
Day 22 — Decided: PDF image processing (figures, charts, OCR, multimodal
Gemini) deferred to v2. v1 is text-layer-only; scanned PDFs fail
gracefully via is_scanned=True. The v2 design decision (multimodal
replace vs augment the chunking pipeline) deliberately not made yet —
revisit after a few weeks of using v1 on actual papers.
```

This matters because the "what about figures?" question WILL resurface — when you read your own code in a month, when Claude Code reads the repo in a fresh session, when someone reviews the project. Recording the decision in one searchable place stops it from being relitigated.

---

## 1. Today's deliverables — at a glance

| # | Deliverable | File | Time |
|---|---|---|---|
| 1 | `parse_pdf(path) -> ParsedPaper` returns title, abstract, sections, raw text | `backend/tools/pdf_parser.py` | 2 hr |
| 2 | Chunking: by headers when detectable, fall back to ~2000-char chunks | same file (or `pdf_chunker.py` — see Decision A) | 1.5 hr |
| 3 | Scanned-PDF detection (no extractable text) returns a clear error | same file | 30 min |
| 4 | Encrypted/corrupt PDF handling — `PDFParseError` exception type | same file | 30 min |
| 5 | PyWebView file-drop handler → WebSocket event with file path | `backend/desktop.py`, `backend/api/voice.py`, `frontend/src/App.tsx` | 1.5 hr |
| 6 | Smoke test script: parse all 3 test PDFs, print structure | `backend/tests/test_pdf_parse.py` | 30 min |

**Total budget: ~6.5 hours.** If running over by Day 22 evening, descope item 5 (file drop) — you can paste a path manually for tomorrow's tool work, file drop is a UX nicety.

**No git commits between items.** End-of-day commit message: `feat: pdf parsing with section detection and chunking`.

---

## 2. Decisions to make before writing code

These are the non-trivial choices I want you to weigh in on. Per CLAUDE.md §"Suggest, don't just write" — I'll lay out the trade-offs and ask before implementing.

### Decision A — File organization: one file or two?

**Option A1: single file `backend/tools/pdf_parser.py`**
- Parsing + chunking + scanned-detection all in one module
- Simpler imports tomorrow when `summarize_paper.py` consumes it
- Risk: by Day 24 the file may push past 300 lines (the soft cap in CLAUDE.md)

**Option A2: split into `pdf_parser.py` + `pdf_chunker.py`**
- Cleaner separation; chunking strategy may evolve independently
- Slight import overhead; tomorrow's tool imports from two files
- Better if hierarchical chunking (Day 23) grows complex

**Recommendation: A1 today.** Total expected size for Day 22 is ~200 lines. Split tomorrow if it crosses 300. Premature splitting is a worse smell than a slightly long file.

### Decision B — Where does the parsed PDF return type live?

The function returns a structured object with title, abstract, sections, chunks. Options:

**Option B1: `Pydantic` model in `backend/models/pdf.py`**
- Consistent with `backend/models/chat.py`, `backend/models/voice.py`
- Pydantic gives free validation + JSON serialization (useful for tomorrow when LLM gets sections back as JSON)
- One more file to create

**Option B2: `dataclass` inline in `pdf_parser.py`**
- Lighter weight; no separate file
- Less consistent with the rest of the codebase

**Recommendation: B1.** Type-hints-everywhere is a CLAUDE.md rule and Pydantic is already the convention. Path: `backend/models/pdf.py` → `ParsedPaper`, `PaperSection`, `PaperChunk`.

### Decision C — Chunk size: characters or tokens?

The plan says "fall back to 2000-char chunks". But Gemini measures context in tokens, and ~1 token ≈ 4 chars in English, ~2-3 chars for technical text with many proper nouns (drug names, gene names).

**Option C1: stick with 2000 chars** (~500-700 tokens). Simple, deterministic. Risk: occasional oversized chunks.

**Option C2: use `tiktoken` for true token counting**. Accurate but adds a dependency. Gemini doesn't use tiktoken (it uses SentencePiece), so this is approximate anyway.

**Option C3: chars now, switch to Gemini's `count_tokens` API in Day 23**. The Gemini SDK has `client.models.count_tokens(model=..., contents=...)`. More accurate, no extra dependency, but one API call per chunk count.

**Recommendation: C1 today, possibly C3 tomorrow.** Day 22 is parsing — don't over-engineer. If Day 23 hits "chunk too long" errors, switch then.

### Decision D — File drop scope today

The Day-by-Day Plan v2 lists "PyWebView: enable file drop, send path via WebSocket" as part of Day 22, but it's a UX feature that needs touching three files. Two options:

**Option D1: full implementation today.** Drag-drop works end-to-end. Path arrives in backend.

**Option D2: defer file drop, accept a path via existing chat endpoint today.** Use `POST /chat` with a message like `"summarize: C:/path/to/paper.pdf"` to test parsing without UI work. Add real file drop on Day 24 (polish day).

**Recommendation: D1 if parsing finishes early; D2 if running over time.** Parsing is the substance — UX can slip a day.

---

## 3. Implementation — step by step

Each step has a "write the docstring first" cue per CLAUDE.md §"Working style 1". Drop the docstring/signature into the file, then ask Claude Code to fill in the body. Read every line before accepting.

### Step 1 — Create the models (`backend/models/pdf.py`)

**Write this signature first:**

```python
"""Pydantic models for parsed PDF papers."""

from pydantic import BaseModel


class PaperSection(BaseModel):
    """A logical section of a paper (Abstract, Methods, etc.).

    `heading` is the detected section header text; `body` is everything
    between this heading and the next one.
    """
    heading: str
    body: str
    page_start: int  # 1-indexed page number where this section begins


class PaperChunk(BaseModel):
    """A chunk of text sized for LLM consumption.

    Chunks may be aligned to sections (preferred) or arbitrary 2000-char
    windows when section detection fails.
    """
    text: str
    source_heading: str | None  # None if from fallback chunking
    chunk_index: int
    char_count: int


class ParsedPaper(BaseModel):
    """The full output of `parse_pdf(path)`.

    `title` and `abstract` are best-effort heuristic extractions.
    `sections` is empty if no headings were detected (in which case
    `chunks` falls back to fixed-size windows over `full_text`).
    """
    title: str | None
    abstract: str | None
    sections: list[PaperSection]
    chunks: list[PaperChunk]
    full_text: str
    page_count: int
    is_scanned: bool  # True if no text layer detected
    source_path: str  # absolute path; for logging
```

**Ask Claude Code to:** check this against the existing models in `backend/models/`, confirm Pydantic v2 syntax, and flag anything that breaks convention.

### Step 2 — Skeleton of `parse_pdf` (`backend/tools/pdf_parser.py`)

**Write this signature + docstring first:**

```python
"""PDF parsing for paper summarization.

Public entry point: `parse_pdf(path)`. Returns a structured ParsedPaper
with title, abstract, sections, and chunks ready for LLM summarization.

Handles three failure modes:
1. File missing / unreadable -> FileNotFoundError or PDFParseError
2. Encrypted PDF -> PDFParseError("encrypted")
3. No text layer (scanned image PDF) -> ParsedPaper with is_scanned=True
   and empty sections/chunks. Caller decides what to do (Day 23 will
   surface a graceful "this PDF appears to be scanned" message to the user).

NOT in scope for this module: LLM summarization, vector storage, voice
integration. Those live in `summarize_paper.py` (Day 23) and the
orchestrator (already in place).

Not supported in v1 (deferred to v2 — see docs/journal.md entry for Day 22):
- OCR on scanned PDFs (is_scanned=True returns gracefully; no Tesseract call)
- Figure / chart / table image extraction or interpretation
- Equation image extraction (LaTeX-source equations extract as garbled text)
- Multimodal summarization (sending PDF bytes directly to Gemini Vision)

The v2 decision is whether multimodal Gemini *replaces* this chunking
pipeline or *augments* it. Don't pre-decide — revisit after a few weeks
of using v1 on actual papers.
"""

from pathlib import Path

import fitz  # pymupdf
from loguru import logger

from backend.models.pdf import ParsedPaper, PaperSection, PaperChunk


class PDFParseError(Exception):
    """Raised when a PDF cannot be parsed (encrypted, corrupt, etc.).

    Scanned PDFs do NOT raise — they return is_scanned=True instead.
    The distinction: PDFParseError is for "couldn't open this file at all";
    scanned PDFs opened fine, they just have no text to extract.
    """


def parse_pdf(path: Path) -> ParsedPaper:
    """Parse a PDF into structured sections and chunks.

    Args:
        path: Absolute path to the PDF file.

    Returns:
        ParsedPaper with title, abstract, sections, chunks, full_text.

    Raises:
        FileNotFoundError: if the file doesn't exist.
        PDFParseError: if the file is encrypted, corrupt, or unreadable.
    """
    # ...implementation per Steps 2a-2e below
```

### Step 2a — Open and validate the document

In `parse_pdf`, after the docstring:

```python
# Open the document. fitz raises on missing file (caught and re-raised
# as FileNotFoundError for caller clarity). Encrypted PDFs open but
# need_pass=True; we treat them as unsupported in v1.
if not path.exists():
    raise FileNotFoundError(f"PDF not found: {path}")
try:
    doc = fitz.open(path)
except fitz.FileDataError as e:
    raise PDFParseError(f"corrupt or unreadable PDF: {e}") from e
if doc.needs_pass:
    doc.close()
    raise PDFParseError("encrypted PDF — not supported in v1")
```

**Watch out:** `fitz.open()` does NOT raise on encrypted PDFs; it returns a doc with `needs_pass=True`. You have to check explicitly. Skipping this means later text extraction silently returns empty strings.

### Step 2b — Extract full text + detect scanned PDFs

```python
# Extract all text first. A PDF is "scanned" if pymupdf gets zero or
# near-zero characters across all pages — the page is an image, not
# a text layer. Threshold: total text < 100 chars across the doc.
full_text_parts: list[str] = []
for page in doc:
    full_text_parts.append(page.get_text("text"))
full_text = "\n".join(full_text_parts)
is_scanned = len(full_text.strip()) < 100

if is_scanned:
    logger.warning(f"pdf appears to be scanned (no text layer): {path}")
    doc.close()
    return ParsedPaper(
        title=None,
        abstract=None,
        sections=[],
        chunks=[],
        full_text="",
        page_count=len(doc),
        is_scanned=True,
        source_path=str(path),
    )
```

**Two `get_text` modes to know:**
- `page.get_text("text")` — plain text, default for our use
- `page.get_text("dict")` — structured, with font sizes/positions; useful for heuristic title detection (Step 2c). Don't use it for the main body; it's slower and you'd have to flatten it.

### Step 2c — Extract title and abstract (heuristic)

This is the messy heuristic part. Two strategies, pick the simpler one:

**Strategy 1 (recommended): largest text on page 1**

```python
# Title heuristic: on page 1, find the text span(s) with the largest
# font size in the top half of the page. Concatenate them. This works
# for ~90% of arxiv papers. Fallback: first non-empty line.
title = _extract_title(doc)
abstract = _extract_abstract(full_text)
```

Then implement `_extract_title` using `page.get_text("dict")` to find max-font spans, and `_extract_abstract` using regex on `full_text` to grab text between "Abstract" and "Introduction"/"1." headers.

**Watch out:** abstract detection by regex is brittle. Pattern that works for most papers:

```python
import re

_ABSTRACT_PATTERN = re.compile(
    r"(?:^|\n)\s*Abstract\s*\n+(.*?)(?:\n\s*(?:1\.|Introduction|Keywords)\b)",
    re.IGNORECASE | re.DOTALL,
)
```

If the regex fails, return `None` for abstract. Don't crash. Day 23's LLM call can still work with `full_text` and section chunks.

### Step 2d — Section detection

```python
# Section heuristic: lines that match common section header patterns
# (numbered "1. Introduction", "1.1 Methods", or all-caps "METHODS").
# Split full_text on these matches; the lines between matches are bodies.
sections = _detect_sections(full_text)
```

Then implement `_detect_sections`:

```python
_SECTION_PATTERN = re.compile(
    r"^(?:"
    r"(?:\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z ]{2,40})"  # "1. Introduction", "2.1 Methods"
    r"|"
    r"(?:[A-Z][A-Z ]{2,40})"                        # "METHODS", "RESULTS"
    r"|"
    r"(?:(?:Abstract|Introduction|Methods?|Results?|Discussion|Conclusion|References|Acknowledgments?)\s*$)"
    r")",
    re.MULTILINE,
)
```

**Important:** keep this regex modest. Overfitting to one paper's format makes it fail on others. If detection fails (zero or one section found), the chunker (Step 2e) falls back to fixed windows — that's fine.

### Step 2e — Chunking

```python
# If sections were detected (>= 2), chunk per section: short sections
# become one chunk; long sections (>2500 chars) are split into ~2000-char
# windows preserving paragraph boundaries.
#
# If no sections detected, chunk full_text into ~2000-char windows.
# Window size 2000 chars chosen per Day-by-Day Plan; should comfortably
# fit ~3-5 chunks in a single Gemini Flash context with room for the
# system prompt and tool schemas.
chunks = _chunk_paper(sections, full_text)
```

Then implement `_chunk_paper`:

```python
def _chunk_paper(
    sections: list[PaperSection],
    full_text: str,
    target_size: int = 2000,
) -> list[PaperChunk]:
    """Chunk sections (preferred) or fall back to fixed windows.

    Splits at paragraph boundaries when possible; never mid-sentence.
    """
    chunks: list[PaperChunk] = []
    idx = 0

    if len(sections) >= 2:
        # Section-aligned chunking
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
                # Split long section on paragraph boundaries
                for window in _split_on_paragraphs(section.body, target_size):
                    chunks.append(PaperChunk(
                        text=window,
                        source_heading=section.heading,
                        chunk_index=idx,
                        char_count=len(window),
                    ))
                    idx += 1
    else:
        # Fallback: fixed windows over full_text
        for window in _split_on_paragraphs(full_text, target_size):
            chunks.append(PaperChunk(
                text=window,
                source_heading=None,
                chunk_index=idx,
                char_count=len(window),
            ))
            idx += 1

    return chunks
```

And `_split_on_paragraphs`:

```python
def _split_on_paragraphs(text: str, target_size: int) -> list[str]:
    """Greedy split: accumulate paragraphs until target_size, then break."""
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for p in paragraphs:
        p_size = len(p)
        if current_size + p_size > target_size and current:
            chunks.append("\n\n".join(current))
            current = [p]
            current_size = p_size
        else:
            current.append(p)
            current_size += p_size
    if current:
        chunks.append("\n\n".join(current))
    return chunks
```

### Step 2f — Assemble and return

```python
result = ParsedPaper(
    title=title,
    abstract=abstract,
    sections=sections,
    chunks=chunks,
    full_text=full_text,
    page_count=len(doc),
    is_scanned=False,
    source_path=str(path),
)
doc.close()
logger.info(
    f"parsed pdf: path={path.name} pages={result.page_count} "
    f"sections={len(sections)} chunks={len(chunks)} "
    f"title={'yes' if title else 'no'} abstract={'yes' if abstract else 'no'}"
)
return result
```

**Watch out:** `doc.close()` is important — fitz holds a file handle. Use a try/finally pattern if anything between open and close can raise. For Day 22's simple flow, an explicit close at the end and inside each early-return is fine.

---

### Step 3 — File drop handler (only if on track; otherwise defer to Day 24)

This is the UX item. Three small touches.

**3a. PyWebView file drop (`backend/desktop.py`):**

PyWebView exposes file drops via a JS API. The cleanest path: expose a Python function to JS that receives the dropped file path, and have it forward to the WebSocket.

Read the existing `backend/desktop.py` first. Ask Claude Code to add a drop handler that:
1. Captures dropped files via PyWebView's `js_api` mechanism
2. Validates the file is a `.pdf` (don't accept arbitrary files)
3. Forwards the path to the FastAPI backend via a `POST /pdf/dropped` route (simpler than WebSocket for one-shot events)

**3b. `POST /pdf/dropped` route (`backend/api/voice.py` or new `backend/api/pdf.py`):**

```python
@router.post("/pdf/dropped")
async def pdf_dropped(payload: PdfDroppedPayload) -> dict:
    """Receive a dropped PDF path from the frontend.

    Today: just broadcasts a 'pdf_received' WebSocket event so the UI can
    acknowledge. Day 23 wires this to the summarize_paper tool.
    """
    path = Path(payload.path)
    if not path.exists() or path.suffix.lower() != ".pdf":
        raise HTTPException(400, "invalid pdf path")
    await ws_manager.broadcast({
        "type": "pdf_received",
        "filename": path.name,
        "path": str(path),
    })
    return {"status": "ok", "filename": path.name}
```

**3c. Frontend acknowledgment (`frontend/src/App.tsx`):**

Just a console log + a chat-panel toast: "Received: paper.pdf — ready for summarize". No tool invocation yet. Tomorrow's work.

**If file drop is too fiddly:** drop this step. For Day 22 verification, manually paste a path: open a Python REPL with the backend running, call `parse_pdf(Path("data/test_pdfs/your_paper.pdf"))`, inspect the result.

---

### Step 4 — Smoke test (`backend/tests/test_pdf_parse.py`)

Per CLAUDE.md §"Codebase conventions" — tests are lightweight, no pytest-everything. A runnable script that prints results is fine.

```python
"""Smoke test for PDF parsing — run manually after Day 22 work."""

from pathlib import Path

from backend.tools.pdf_parser import parse_pdf, PDFParseError


TEST_PDF_DIR = Path("data/test_pdfs")


def main():
    for pdf in TEST_PDF_DIR.glob("*.pdf"):
        print(f"\n{'='*60}\nPDF: {pdf.name}\n{'='*60}")
        try:
            result = parse_pdf(pdf)
        except (FileNotFoundError, PDFParseError) as e:
            print(f"  ERROR: {e}")
            continue

        print(f"  pages:    {result.page_count}")
        print(f"  scanned:  {result.is_scanned}")
        print(f"  title:    {(result.title or '<none>')[:80]}")
        print(f"  abstract: {(result.abstract or '<none>')[:120]}...")
        print(f"  sections: {len(result.sections)}")
        for s in result.sections[:5]:
            print(f"    - {s.heading[:60]}  ({len(s.body)} chars, p.{s.page_start})")
        if len(result.sections) > 5:
            print(f"    ... +{len(result.sections) - 5} more")
        print(f"  chunks:   {len(result.chunks)}")
        for c in result.chunks[:3]:
            heading = c.source_heading or "<fallback>"
            print(f"    [{c.chunk_index}] {heading[:30]} ({c.char_count} chars)")


if __name__ == "__main__":
    main()
```

Run with: `python -m backend.tests.test_pdf_parse`

---

## 4. Verification — what to check before calling Day 22 done

Per CLAUDE.md §"Stay in the read-review loop" — exactly what to test.

```
1. Imports work:
   python -c "from backend.tools.pdf_parser import parse_pdf; from backend.models.pdf import ParsedPaper; print('OK')"

2. Clean arxiv paper parses with sections:
   python -m backend.tests.test_pdf_parse
   → for the clean paper: sections >= 3, chunks >= 3, title detected, abstract detected

3. Long paper produces many chunks:
   → page_count > 30, chunks > 10, no chunk exceeds 2500 chars (allow some overflow at paragraph boundaries)

4. Scanned PDF returns gracefully:
   → is_scanned=True, sections=[], chunks=[], full_text=""
   → NO exception raised

5. Bad path raises cleanly:
   python -c "from pathlib import Path; from backend.tools.pdf_parser import parse_pdf; parse_pdf(Path('nonexistent.pdf'))"
   → FileNotFoundError, not a fitz internal error

6. Encrypted PDF (if you have one) raises PDFParseError:
   → "encrypted PDF — not supported in v1"

7. (If Step 3 done) File drop works:
   → drag a PDF onto the floating window
   → log shows: "pdf_dropped: path=..., filename=..."
   → chat panel acknowledges: "Received: paper.pdf"

8. (If Step 3 done) Invalid file drop rejected:
   → drag a .txt file
   → HTTP 400, no crash, log shows "invalid pdf path"

9. Backend startup is clean:
   → no new warnings, no import errors, "tools registered: 5" still (no new tool today)
```

If 1–6 pass, Day 22 core is done. 7–8 are bonus.

---

## 5. Watch out for

The gotchas most likely to bite. Each was discovered the hard way by someone before.

- **`import fitz` not `import pymupdf`.** The package name is `pymupdf` for pip; the module name is `fitz` for legacy reasons. Don't fight it.

- **`fitz.open()` does not raise on encrypted PDFs.** It returns a doc with `needs_pass=True`. Forget the check → text extraction silently returns empty strings → looks like a scanned PDF → confusing debug session.

- **`fitz.FileDataError` is the catchable corrupt-file exception.** Not `FileNotFoundError`, not `IOError`. Test by feeding it a `.txt` renamed to `.pdf`.

- **`page.get_text("text")` defaults to reading order based on layout.** For two-column papers this usually works correctly — pymupdf reorders by column. But for unusual layouts (sidebars, floating figures) you may see interleaved text. Don't try to fix this in v1; the LLM in Day 23 is robust to mildly mangled input.

- **Title heuristics fail on papers with stylized covers** (some Springer/Elsevier journals). The "largest font" heuristic catches the journal logo instead. If this happens for your test paper, accept it for v1 — abstract + sections + full_text are enough for the LLM. Title is nice-to-have.

- **`page_count` from `len(doc)` is computed before `doc.close()`.** If you close first and then read `len`, you get a "document closed" error. Read it before the close call.

- **Don't import the parser into `backend/main.py` lifespan yet.** Today's `pdf_parser.py` is library code, not a tool. The `@registry.register` decorator and the lifespan import line are for `summarize_paper.py` tomorrow. Day 22 result: `tools registered: 5` (unchanged from Day 21).

- **`re.split(r"\n\s*\n", text)` for paragraphs is naive but works.** A more correct approach uses pymupdf's block-level extraction, but the gain is marginal for paper text and the complexity isn't worth it today.

- **2000-char chunks may exceed 2500 chars after paragraph-boundary preservation.** That's fine — the chunker greedily completes the last paragraph rather than splitting mid-sentence. Gemini Flash has 1M context; a 2500-char chunk is nothing.

- **Memory note: don't `read()` the whole PDF into RAM before parsing.** `fitz.open(path)` streams. The full_text concatenation in Step 2b is the largest in-memory object (~100KB for a typical paper). Don't optimize prematurely.

---

## 6. What gets touched today — file list

```
NEW:
  backend/models/pdf.py                   -- ParsedPaper, PaperSection, PaperChunk
  backend/tools/pdf_parser.py             -- parse_pdf() + helpers + PDFParseError
  backend/tests/test_pdf_parse.py         -- smoke test script
  data/test_pdfs/                         -- 3 test PDFs (gitignored)

EDIT (only if Step 3 done):
  backend/desktop.py                      -- pywebview drop handler
  backend/api/voice.py (or new pdf.py)    -- POST /pdf/dropped route
  frontend/src/App.tsx                    -- pdf_received WS event ack

EDIT (always):
  backend/requirements.txt                -- pymupdf pinned version
  .gitignore                              -- data/test_pdfs/ if not already
```

**No edits to:** `backend/services/conversation.py`, `backend/tools/registry.py`, `backend/main.py` lifespan, `backend/prompts/system/50_tools.md`. Those are tomorrow.

---

## 7. End-of-day commits

Per CLAUDE.md §"Codebase conventions" — logical chunks, not "wip".

```
[ ] feat(models): pydantic models for parsed pdf papers
[ ] feat(tools): pdf parsing with section detection and chunking
[ ] test: smoke test for pdf parsing across clean/long/scanned cases
[ ] feat(ui): pywebview file drop forwards pdf path to backend   (if Step 3 done)
[ ] chore: pin pymupdf in requirements
```

---

## 8. Heads-up for Day 23

Day 23 is the substance day — wiring `parse_pdf` into an LLM-driven `summarize_paper` tool with structured output (JSON mode). Things Day 23 will need that Day 22 sets up:

- **`ParsedPaper.chunks`** feeds hierarchical summarization: summarize each chunk → synthesize a final summary from the chunk summaries. Worked example shape Day 23 will follow:
  ```
  for chunk in paper.chunks:
      chunk_summary = await llm.generate(SUMMARIZE_CHUNK_PROMPT.format(chunk.text))
      chunk_summaries.append(chunk_summary)
  final = await llm.generate(SYNTHESIZE_PROMPT.format("\n".join(chunk_summaries)))
  ```
- **`ParsedPaper.is_scanned=True`** is the signal for Day 23's tool to return a graceful "this PDF appears to be scanned, OCR isn't supported in v1" message instead of attempting summarization.
- **Tool name will be `summarize_paper`**, parameters `{"path": str}`. Registers like the existing four memory tools.
- **MUTED re-check between chunks** — summarization may take 30+ seconds for a long paper; the user might hit mute mid-way. See `voice-pipeline/SKILL.md §"Adding tool calls inside THINKING"`.
- **Gemini JSON mode** — Day 23 will use `response_mime_type="application/json"` + `response_schema=...` for the structured output (key_claims, methods, results, limitations, relevance_to_user). Verify the SDK syntax against the installed `google-genai 2.6.0` before writing the call.
- **Image processing is OUT of scope for Days 22-24 (deferred to v2).** The `summarize_paper` tool description must NOT promise figure / chart / table analysis. If a user asks "what does Figure 3 show?", the tool should answer from the caption text only and say so. The v2 design decision (multimodal replace vs augment) is documented in the `parse_pdf` module docstring; don't resolve it this week.

Day 22 leaves all of these ready. Today is plumbing day. Tomorrow is the day the magic happens.
