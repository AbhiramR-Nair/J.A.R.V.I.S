# Project Status — Day 22

**Period covered:** Day 22 (Week 4, Day 3 — PDF Parsing Foundation)
**Status:** Complete — parser ships, smoke test passes across all edge cases.
**Environment:** Windows 11, Python 3.13.5, pymupdf==1.27.2.3, google-genai==2.6.0

> Checkpoint summary for Day 22: what got built, why it was built that way, what went
> sideways, and what Day 23 needs to know. Read before Day 23.

---

## 1. What has been done

| Task | What landed | Status |
|---|---|---|
| Pre-flight — Gemini quota check | `gemini-2.5-flash` available; `gemini-2.0-flash` still rate-limited (429). `gemini_model_heavy` in `settings.py` updated to `gemini-2.5-flash`. `gemini_model` stays on `gemini-flash-lite-latest` to conserve quota. | Done |
| Pre-flight — pymupdf install | `pip install pymupdf` (v1.27.2.3). `import fitz` verified. Pinned in `backend/requirements.txt`. | Done |
| Pre-flight — test PDFs | `data/test_pdfs/` created (gitignored). Two review papers placed: 29-page protein engineering paper, 7-page gene expression paper. | Done |
| Pre-flight — journal entry | v2-deferral decision recorded in `docs/journal.md`: image/OCR/multimodal Gemini deferred to v2; v1 is text-layer-only. | Done |
| T-1 — `backend/models/pdf.py` | Three Pydantic v2 models: `PaperSection` (heading, body, page_start), `PaperChunk` (text, source_heading, chunk_index, char_count), `ParsedPaper` (title, abstract, sections, chunks, full_text, page_count, is_scanned, source_path). | Done |
| T-2 — `backend/tools/pdf_parser.py` | `parse_pdf(path) -> ParsedPaper`. Handles: FileNotFoundError (no file), PDFParseError (encrypted/corrupt), is_scanned=True (no text layer). Internal helpers: `_extract_title` (largest font on page 1), `_extract_abstract` (regex between Abstract and Introduction), `_detect_sections` (header regex with `\s*$` end-of-line anchor), `_chunk_paper` (section-aligned or fallback fixed windows), `_split_on_paragraphs` (greedy paragraph-boundary split). | Done |
| T-3 — `backend/tests/test_pdf_parse.py` | Manual smoke test script. Iterates all PDFs in `data/test_pdfs/`, prints: pages, scanned flag, title, abstract preview, sections list, chunk count + previews, max/avg chunk stats. | Done |
| T-4 — Section detection tuning | Two-round fix to section over-detection (see §3). Final: body >= 100 chars filter + `\s*$` end-of-line anchor + ALL-CAPS min 5 chars. | Done |
| Verification — all checklist items | All 7 verification items passed (see §5). `tools registered: 5` unchanged. | Done |

---

## 2. Implementation strategy — the *why* behind non-obvious choices

### Decision A — One file: `pdf_parser.py` (not split)

Chose Option A1 from the plan: parsing + chunking + detection in one file. Expected ~200 lines; actual is ~240 lines — within the soft cap. No premature split. If Day 23's `summarize_paper.py` needs to extend the chunking logic substantially, split then.

### Decision B — Pydantic models in `backend/models/pdf.py`

Consistent with the rest of the codebase (`chat.py`, `voice.py`). Pydantic gives free JSON serialisation — relevant for Day 23 when `ParsedPaper` fields will be serialised into tool results and LLM prompts. `str | None` union syntax throughout (Python 3.10+ style already used in this codebase).

### Decision C — 2000-char chunk target, paragraph-boundary splitting

Stayed with the plan's `target_size=2000` chars. The `_split_on_paragraphs` helper never cuts mid-paragraph — chunks may exceed 2000 chars by one paragraph length. Observed max: ~4400 chars, which is well within Gemini Flash's 1M context window. No `tiktoken` or Gemini `count_tokens` — Day 23 will evaluate whether this is needed based on actual API behaviour.

### Decision D — File drop deferred to Day 24

Parsing (T-1 through T-3) consumed the full 6-hour budget. File drop is a UX nicety; Day 23's tool work is the substance. File drop will be added on Day 24 (polish/arxiv day).

### `PDFParseError` vs `is_scanned=True` — the exception split

Encrypted/corrupt PDFs raise `PDFParseError` because they cannot be opened at all. Scanned PDFs open fine but yield no text — they return `ParsedPaper(is_scanned=True)` without raising. This split lets Day 23's tool handler give different user-facing messages:
- `PDFParseError` → "I couldn't open that PDF — it may be encrypted or damaged."
- `is_scanned=True` → "That PDF appears to be scanned. Text extraction isn't supported in v1."

### `_extract_title` — largest font on page 1, top 60%

Uses `page.get_text("dict")` to access per-span font sizes. Restricts to the top 60% of page height to avoid captions and footnotes. Falls back to the first non-empty text line if no large span is found. Known limitation: HHS/PubMed author manuscripts print "HHS Public Access" as the first large text — the watermark wins. This is documented in the Day 22 module docstring; title is nice-to-have.

### `_detect_sections` — regex with `\s*$` anchor (critical design choice)

The initial regex (`^...[A-Z]{2,50}`) matched every ALL-CAPS abbreviation in body text (PEG, DNA, CPD, USA) producing 167 false sections on a 29-page paper. Two-round fix:

1. **Body length filter (≥100 chars):** eliminates numbered list items with empty bodies. Got: 167 → 143 sections. Not enough.
2. **`\s*$` end-of-line anchor on ALL-CAPS and numbered patterns:** real section headers sit alone on their own line; inline abbreviations ("PEG ylation [8, 9]…") have more text on the same line. Combined with raising ALL-CAPS minimum from 3 to 5 chars. Got: 143 → 3–7 sections. ✓

The `\s*$` anchor is the load-bearing fix. Without it, the regex matches anything that starts with a capital on a new line, regardless of what follows.

### Library code, not a tool

`pdf_parser.py` has no `@registry.register` decorator. It is library code consumed by `summarize_paper.py` (Day 23). `tools registered: 5` is unchanged. The import in Day 23's tool module will be `from backend.tools.pdf_parser import parse_pdf, PDFParseError`.

---

## 3. Problems faced and how they were handled

### Section over-detection: 167 false sections on a 29-page paper

**Symptom:** smoke test showed 167 sections and 180 chunks on a 29-page review paper. Average chunk size was 458 chars — far too small for meaningful LLM summarization.

**Root cause:** the `[A-Z][A-Z ]{2,50}` ALL-CAPS pattern matches 3-letter abbreviations (PEG, USA, DNA, CPD) that appear at the start of sentences after a newline. The `re.MULTILINE` flag makes `^` match line-starts throughout the document, so every new paragraph starting with a capitalized abbreviation was detected as a "section header".

**Round 1 fix:** added `len(body.strip()) >= 100` filter to `_detect_sections`. Eliminated sections with empty bodies (numbered list items). Result: 167 → 143. Insufficient.

**Round 2 fix (the real fix):**
- Added `\s*$` to end of ALL-CAPS and numbered patterns — headers must be the only content on their line.
- Raised ALL-CAPS minimum from `{2,50}` (3 chars total) to `{4,50}` (5 chars total) — eliminates DNA, PEG, USA, HEK.
- Result: 167 → 3–7 genuine sections. Average chunk size increased to 1543–3247 chars. ✓

**Lesson:** for regex-based heuristics on free-form text, the end-of-line anchor (`\s*$`) is more discriminating than minimum-length constraints alone. Real headers are structurally isolated; abbreviations are embedded.

### `gemini-2.0-flash` still rate-limited (Day 21 carryover)

**Symptom:** `gemini-2.0-flash` returns 429 RESOURCE_EXHAUSTED.

**Fix applied:** `gemini_model_heavy` updated to `gemini-2.5-flash` (confirmed available). `gemini_model` stays on `gemini-flash-lite-latest`.

**Impact on Day 22:** no LLM calls made today (parsing only), so no quota impact.

---

## 4. Heads-up for Day 23 and beyond

### Check Gemini model availability before Day 23 — critical

Day 23 makes many LLM calls (one per chunk for map-reduce summarization). With ~26 chunks for the 29-page paper, a single summarization run could make 26+ Gemini calls. Before starting:

```bash
python -c "
import asyncio
from google import genai
from backend.config.settings import get_settings
s = get_settings()
client = genai.Client(api_key=s.gemini_api_key)
async def t():
    for m in ['gemini-2.5-flash', 'gemini-2.0-flash']:
        try:
            r = await client.aio.models.generate_content(model=m, contents='ping')
            print(f'OK  {m}')
        except Exception as e:
            print(f'NO  {m}: {str(e)[:60]}')
asyncio.run(t())
"
```

- If `gemini-2.5-flash` responds → `gemini_model_heavy` is already set correctly. Proceed.
- If `gemini-2.0-flash` also available → consider switching `gemini_model_heavy` to `gemini-2.0-flash` to save `gemini-2.5-flash` quota for complex reasoning.
- If both are rate-limited → proceed with `gemini-flash-lite-latest` but expect lower structured-output quality. Consider enabling billing on Google AI Studio (~$5/month eliminates this problem permanently).

### Chunk size for paper 1 is large (avg 3247 chars)

The 29-page paper has only 3 detected sections. The entire body falls into the "Abstract" section (49k chars), which is then split into 22 fixed-window chunks averaging ~3247 chars. This is above the 2000-char target but within Gemini's context.

**Day 23 implication:** the map-reduce loop will make ~26 chunk-summary calls before synthesizing. Each call is independent — the MUTED re-check between iterations (documented in `voice-pipeline/SKILL.md`) will give the user clean exit points.

**Possible Day 24 improvement:** if summarization quality is poor for large chunks, reduce `target_size` from 2000 to 1500. Do not pre-optimize — test Day 23 first.

### `ParsedPaper.is_scanned=True` path needs a clear user message in Day 23

When `is_scanned=True`, `summarize_paper` must return a human-readable string, not crash or attempt summarization. Suggested message: `"That PDF appears to be scanned — it has no text layer I can read. OCR support is planned for a future version."` Return this string from the tool handler; the LLM will speak it.

### Title heuristic fails on PubMed HHS manuscripts

The "HHS Public Access" watermark is larger than the actual title on page 1 of NIH-funded papers. Title is nice-to-have — Day 23's summarizer prompt should not rely on `ParsedPaper.title` being the actual paper title. Use `source_path` for identification in logs.

### `source_heading=None` in chunks signals fallback chunking to Day 23

When sections are not detected, all chunks have `source_heading=None`. Day 23's chunk-summary prompt should handle this:
- `source_heading` is set → `"Summarize this section: {heading}\n\n{text}"`
- `source_heading` is None → `"Summarize this passage from the paper:\n\n{text}"`

### File drop not yet wired (deferred to Day 24)

The UI currently has no drag-and-drop handler for PDFs. For Day 23 testing, pass the PDF path directly to the `summarize_paper` tool via voice: `"summarize the paper at data/test_pdfs/Biomedical application of protein engineering.pdf"`. The LLM will extract the path from the utterance and pass it to the tool.

Alternatively, add a temporary `POST /pdf/dropped` endpoint stub early in Day 23 if voice path-passing proves unreliable.

---

## 5. How to verify Day 22

```
1. Imports clean:
   python -c "from backend.tools.pdf_parser import parse_pdf, PDFParseError; from backend.models.pdf import ParsedPaper; print('OK')"
   → "OK"

2. Smoke test across test PDFs:
   python -m backend.tests.test_pdf_parse
   → Both papers: no exceptions, sections >= 3, chunks >= 3, title detected

3. Scanned PDF detection (blank page):
   → is_scanned=True, sections=[], chunks=[], no exception

4. Bad path:
   python -c "from pathlib import Path; from backend.tools.pdf_parser import parse_pdf; parse_pdf(Path('nonexistent.pdf'))"
   → FileNotFoundError

5. Corrupt file (txt renamed .pdf):
   → PDFParseError

6. Tools unchanged:
   (simulate lifespan imports) len(registry) == 5

All checks confirmed passing on 2026-05-29.
```

---

## 6. Files changed this day

```
NEW:
  backend/models/pdf.py                   -- ParsedPaper, PaperSection, PaperChunk models
  backend/tools/pdf_parser.py             -- parse_pdf() + helpers + PDFParseError
  backend/tests/test_pdf_parse.py         -- manual smoke test script
  data/test_pdfs/                         -- 2 test PDFs (gitignored)
  docs/project_status/PROJECT_STATUS(DAY_22).md  -- this file
  docs/plans/day_22_plan.md               -- day plan (committed for record)

EDIT:
  backend/config/settings.py             -- gemini_model_heavy → gemini-2.5-flash
  backend/requirements.txt               -- pymupdf==1.27.2.3 pinned
  docs/journal.md                        -- Day 22 v2-deferral entry + end-of-day summary
```

---

## 7. Commits

```
[ ] feat(models): pydantic models for parsed pdf papers
[ ] feat(tools): pdf parsing with section detection and chunking
[ ] test: smoke test for pdf parsing across clean and edge cases
[ ] chore: pin pymupdf and switch gemini_model_heavy to gemini-2.5-flash
[ ] docs: day 22 plan, project status, and journal entry
```
