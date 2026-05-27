# Day 17 Plan — Window Polish + HUD Aesthetic

**Week 3, Day 3.** The final day of substantive Week 3 work before buffer days. The orb has presence; today the *frame around it* gets the same care.

---

## Goal for the day

Strip the dev scaffolding, replace ad-hoc status UI with a coherent HUD aesthetic (bracket-framed panels, mono uppercase labels, characterful header band, instrument-like bottom voice status bar), and make the settings panel a real feature behind a gear toggle. Add the project switcher so voice commands aren't the only way to change active project.

By end of day: the window looks intentional, every visual element earns its place, and a stranger glancing at a screenshot understands what the assistant is doing without reading a word.

Snap-to-corner, minimize-to-tray, and possible click-through research are explicitly deferred to Day 18.

---

## Final UI decisions locked from the mockup conversation

| Decision | Choice |
|---|---|
| Settings panel | **Gear toggle** in header band — hidden by default |
| Chat panel | **Collapses when empty** — already does this; preserve behavior |
| Bottom waveform bars | **Keep** — 7 mini bars, amplitude-driven |
| Color palette | **Cyan/teal accents** (`#7CD4E8` primary) — harmonizes with orb |
| Panel header pattern | `[icon] LABEL` left + `[VALUE]` right, mono uppercase, 0.15–0.18em tracking |
| Header band | `● JARVIS // MK 0` left + gear + close right; doubles as drag handle |
| Bracket frames | 4 corner markers via CSS, applied via reusable `HudFrame` component |
| Icon library | **Lucide React** (new dependency, ~3kb per icon, idiomatic React + Tailwind pick) |

---

## Pre-flight cleanup (do these first, ~40 min total)

### CL-1. Remove the dev cycler and amplitude slider

**File:** `frontend/src/App.tsx`

Search for `import.meta.env.DEV`. There's exactly one block (per the Day 16 status doc) containing both the state cycler buttons AND the amplitude slider. Delete the entire block.

Also delete:
- `const [devAmp, setDevAmp] = useState(...)` (or however it's named)
- Any `setDevAmp` calls

### CL-2. Remove the inline status badge and "J.A.R.V.I.S — online" placeholder

**File:** `frontend/src/App.tsx`

These are being replaced by the new bottom voice status bar (T-5). Delete:
- The `<div>` rendering `Status: {statusLabel}`
- The `<div>` rendering `J.A.R.V.I.S — online`
- The `<div>` rendering the lowercase voice state debug label
- The `statusLabel` computation and any imports it dragged in (if anything becomes unused after deletion, remove)

### CL-3. Remove the Ping backend button

**File:** `frontend/src/App.tsx`

The "Ping backend" button and `ping` state were Day 3 smoke tests for the `/health` endpoint. They've outlived their usefulness. Delete:
- `const [ping, setPing] = useState<string | null>(null)`
- The `pingBackend()` function
- The button + `ping` display JSX
- The `API_BASE` import if it's no longer used (it almost certainly still is, for other endpoints)

### CL-4. (Optional) Tune `tts_calibration_max`

**File:** `backend/config/settings.py` or `.env`

Open item from Day 16. Only do this if TTS reactivity actually feels off in normal use. If it feels right, skip. If TTS amplitude appears pinned high or near zero throughout, adjust per the Day 16 diagnostic procedure (log `latest_amplitude` once/sec during a known response; target 0.2–0.8 range).

Time-box: 15 min. If you can't dial it in cleanly in that window, leave a journal note and move on.

---

## Tasks

### T-1. HUD foundation: the `HudFrame` component

**File:** `frontend/src/components/HudFrame.tsx` (new)

A small wrapper component that adds the four L-shaped corner brackets to any child content. Pure presentation, zero logic.

**Props:**

```ts
interface HudFrameProps {
  children: React.ReactNode;
  /** Tint of the corner brackets. Defaults to the cyan-with-alpha standard. */
  cornerColor?: string;
  /** Internal padding. Defaults to '10px 12px'. */
  padding?: string;
  /** Optional background tint. Defaults to a very faint cyan wash. */
  background?: string;
  /** Optional className passthrough for layout/spacing from parent. */
  className?: string;
}
```

**Implementation notes:**
- Use `position: relative` on the wrapper.
- Four child divs absolutely positioned at the four corners, each with two `border-{side}` declarations to make the L shape.
- Corner size 8×8px, border width 1px, color via the `cornerColor` prop.
- Children render inside normally — no clipping.

Write the prop interface yourself first, then ask for implementation. This component will be used 3–4 times today, so getting it right pays back immediately.

### T-2. Install Lucide React

```bash
cd frontend
npm install lucide-react
```

We'll use these icons today: `Mic`, `MessageSquare`, `Settings`, `X`, `ChevronDown`. About 12 KB total tree-shaken.

Verify it builds. If for any reason the install fails or the bundle size feels wrong, the fallback is inline SVG icons (more code in components, no dep) — but Lucide is the standard pick.

### T-3. Header band

**File:** `frontend/src/components/HeaderBand.tsx` (new)

Replaces the current drag bar in `App.tsx`. Same height (32px), same drag behavior, more character.

**Layout:**

```
┌─────────────────────────────────────────────┐
│ ● JARVIS // MK 0              [gear] [×]   │
└─────────────────────────────────────────────┘
```

- Left cluster: 6×6px filled dot + `JARVIS // MK 0` mono uppercase, 11px, 0.18em tracking, color `#7CD4E8`.
- Right cluster: gear icon button + close X button. Both 14px Lucide icons, semi-transparent cyan on hover.
- The whole bar carries `WebkitAppRegion: "drag"`. The two buttons override with `WebkitAppRegion: "no-drag"`.
- Bottom 1px border with very low alpha cyan (`rgba(124, 212, 232, 0.10)`) for a faint separator.

**Props:**

```ts
interface HeaderBandProps {
  onToggleSettings: () => void;
  onClose: () => void;
  /** Optional: dot color reflecting connection state. Default cyan. */
  statusDotColor?: string;
}
```

The status dot color is set up for future use (drive from WebSocket connection state — amber while reconnecting, red when offline), but today it defaults to cyan and stays static. Cheap forward investment.

### T-4. Settings panel — gear toggle + sections

**Files:** `frontend/src/components/SettingsPanel.tsx` (heavy edit), `frontend/src/App.tsx` (wire toggle state)

**Current state:** the existing `SettingsPanel` has a mic test from Day 12 work. It needs to:
1. Be hidden by default, opened by the gear icon in the header band.
2. Get the HUD aesthetic — wrapped in `HudFrame`, mono uppercase labels.
3. Gain sections: mic device dropdown, project switcher, hotkey display.
4. Close on Escape key or by clicking the gear again.
5. Animate in/out using Framer Motion (already in the stack).

**App-level state:**

```ts
// In App.tsx
const [settingsOpen, setSettingsOpen] = useState(false);

// Escape closes settings
useEffect(() => {
  if (!settingsOpen) return;
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") setSettingsOpen(false);
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}, [settingsOpen]);
```

**Panel structure (when open):**

```
┌─ SETTINGS ──────────────── [3 ITEMS] ─┐
│                                        │
│ MIC DEVICE         [Realtek Default ▾] │
│ PROJECT            [kinase ▾]          │
│ HOTKEY             ALT + SPACE         │
│                                        │
│ [ test mic ]                           │
└────────────────────────────────────────┘
```

- Use `HudFrame` as the wrapper.
- Each row: 2-column layout — label left (10px mono, 0.1em tracking, ~50% alpha), value right (10–11px mono, full alpha).
- Label colors: dim cyan/gray, ~50% alpha.
- Value colors: bright when the value is an active/important state (like the current project), normal alpha otherwise.
- Animate the whole panel with Framer Motion: opacity 0 → 1, translateY 4px → 0, ~200ms ease.

**Sections:**

- **MIC DEVICE** — `<select>` populated from existing `GET /audio/devices` (per the Day 12 voice-pipeline skill). Selection persists via existing `POST /audio/device`. No new backend work.
- **PROJECT** — see T-5 (depends on backend project endpoints existing). Until those land, render `[GENERAL]` static.
- **HOTKEY** — read-only static text `ALT + SPACE` and `CTRL + ALT + J` for now. Display-only.
- **TEST MIC button** — keep the existing functionality, restyled to match (mono uppercase, bracket-frame-bordered button or simple cyan-bordered).

### T-5. Project switcher (backend + frontend)

This is the biggest unknown going in. Backend project switching logic exists at the data layer (`sqlite_store.set_active_project`, `list_projects` per the architecture skill) — but **whether HTTP endpoints exist for the frontend to call them, I don't know yet.**

**First step (5 min, before building anything):** verify the current state.

```bash
# In the repo
grep -rn "projects" backend/api/
# Or check the backend's route list:
curl http://localhost:8000/openapi.json | python -m json.tool | grep -A2 "projects"
```

**Two paths from there:**

**Path A — endpoints already exist:** great, skip to the frontend wiring below. Probably takes ~15 minutes.

**Path B — endpoints need to be built:** add to `backend/api/` (probably a new `projects.py` file or extend an existing route file). Required endpoints:

```python
# GET /projects -> [{ id: int, name: str, is_active: bool }]
@router.get("/projects")
async def list_projects(): ...

# POST /projects/active -> { name: str } -> 200
@router.post("/projects/active")
async def set_active_project(body: SetActiveProjectRequest): ...
```

Both should be thin wrappers around the existing `sqlite_store` functions. Pydantic models for the request/response in `backend/models/`. Project-scoped persistence is already in the DB (`is_active` boolean column from Day 5).

**Frontend wiring:**

In `SettingsPanel.tsx`:
- On mount (or when settings opens), `fetch('/projects')` and populate dropdown options.
- On change, `POST /projects/active` with the new name. On success, update local state.
- Display the active project's name prominently.

The active project will also need to broadcast a state-changed-style WebSocket event if you want other UI to react (e.g., showing the project name in the header band). **Out of scope for Day 17** — for today, the settings panel showing the value is enough.

**Time estimate:** 15 min if Path A, 60 min if Path B.

### T-6. Bottom voice status bar

**File:** `frontend/src/components/StatusBar.tsx` (new — note: there was an empty stub of this name that we deleted on Day 15; the architecture skill is already updated)

The single component doing the most aesthetic work today. Replaces the inline status badge in `App.tsx`.

**Layout:**

```
┌─ [mic]  ▮▮▮▯▯▯▯   ● LISTENING ─┐
│                   MIC OPEN · GROQ STT │
└──────────────────────────────────────┘
```

Wrapped in `HudFrame`. Fixed to the bottom of the window via parent layout (not `position: fixed` — Day 17 doesn't change windowing).

**Props:**

```ts
interface StatusBarProps {
  voiceState: VoiceStateLiteral;
  amplitudeRef: { current: number };  // Day 16 ref type pattern
}
```

**Visual elements (left to right):**

1. **Mic icon** (Lucide `Mic`, 14px). Color shifts with state — see palette below.
2. **Waveform** — 7 mini bars, 2px wide each, 2px gap, ~14px tall area. Heights driven by amplitude with a center-weighted bell curve (bars closer to center react more than outer ones). Updated in a separate `requestAnimationFrame` loop inside this component reading `amplitudeRef.current`.
3. **Right cluster** (stacked, right-aligned):
   - Top line: `● [STATE]` — 10px mono, 0.18em tracking. Dot character precedes the state name.
   - Bottom line: subtext — 9px mono, 0.1em tracking, 40% opacity. Content shown per state (see below).

**Per-state palette + subtext:**

| voiceState | Status text color | Mic icon | Subtext |
|---|---|---|---|
| `idle` | `#7CD4E8` (cyan) | dim cyan | `READY · ALT+SPACE` |
| `listening` | `#9FE1CB` (teal) | teal | `MIC OPEN · GROQ STT` |
| `transcribing` | `#9FE1CB` (teal, same as listening) | teal | `TRANSCRIBING…` |
| `thinking` | `#B5D4F4` (soft blue) | soft blue | `GEMINI 2.5 FLASH` |
| `speaking` | `#C0DD97` (olive-green) | olive | `TTS · PIPER` |
| `muted` | `rgba(229,240,245,0.4)` (gray) | gray | `MUTED · CTRL+ALT+J` |
| `error` | `#F09595` (red) | red | `ERROR · RETRY IN 3s` |

These colors are pulled from the orb's existing palette so they harmonize with whatever the orb is doing.

**Waveform implementation note:** the bars share `amplitudeRef` with the blob. Two consumers reading the same ref each frame is fine — both run their own RAF loops, both read `amplitudeRef.current`, no coordination needed. The waveform's center-weighted heights:

```ts
for (let i = 0; i < 7; i++) {
  const centerDist = Math.abs(i - 3) / 3;  // 0 at center, 1 at edges
  const factor = 1 - centerDist * 0.4;     // edges respond 60% as much
  const h = voiceState === 'idle' ? 3 : Math.max(3, 3 + amp * 11 * factor);
  // ... write to bar element ...
}
```

### T-7. Chat panel HUD treatment

**File:** `frontend/src/components/ChatPanel.tsx` (light edit)

The existing chat panel:
- Returns `null` when messages are empty ✓ (keep this — confirmed by Day 17 design call)
- Currently shows ALL messages with simple styling

Changes:
1. Wrap the entire `<ul>` content in `<HudFrame>`.
2. Add a header row inside the frame: `[icon] CHAT [N RECENT]` — mono uppercase, same pattern as settings.
3. **Limit to last 5 messages.** Slice the messages array: `messages.slice(-5)`.
4. Restyle message rows: use mono font, prefix with `>` for user / `<` for assistant. Subtle color differentiation (user message text slightly muted, assistant slightly more prominent).

Don't change the `ChatMessage` type — keep the existing `{ role, text }` shape.

### T-8. Wire it all up in `App.tsx`

**File:** `frontend/src/App.tsx`

After cleanup (CL-1 to CL-3), App.tsx will be much simpler. New structure:

```tsx
return (
  <div className="flex flex-col h-screen relative">
    <HeaderBand
      onToggleSettings={() => setSettingsOpen(s => !s)}
      onClose={closeApp}
    />

    <div className="flex flex-col items-center flex-1 gap-4 pt-6 pb-20">
      <Blob voiceState={voiceState} size={180} amplitudeRef={amplitudeRef} />

      {settingsOpen && (
        <SettingsPanel onClose={() => setSettingsOpen(false)} />
      )}

      <ChatPanel messages={messages} />

      {errorToast && <ErrorToast message={errorToast} />}
    </div>

    <div className="absolute bottom-3 left-3 right-3">
      <StatusBar voiceState={voiceState} amplitudeRef={amplitudeRef} />
    </div>
  </div>
);
```

Pull the error toast into its own tiny component while we're here — purely a cleanup nicety. Keep its existing 3s auto-clear behavior.

**Notes:**
- The status bar uses `position: absolute` within the App's `relative` parent — not `position: fixed`. This means it scrolls with the window if content overflows, which is the desired behavior.
- `pb-20` on the content area gives the status bar room to live without overlapping the chat or settings panels.

---

## What's deliberately out of scope today

- **Snap-to-corner drag behavior** — Day 18.
- **Minimize to system tray** — Day 18. Requires `pystray` or similar backend dependency + tray icon assets.
- **Click-through when idle** — descoped to "v2 if ever" per earlier conversation. Genuinely hard on Windows; not worth the time risk.
- **API-health diagnostic panel** — pushable to Day 18 buffer if you want it; needs backend health endpoints.
- **Project name in the header band** — would need a WebSocket event when active project changes. Out of scope for Day 17; settings panel is enough.
- **Connection state driving the header dot color** — forward-prepared (prop exists), but the wiring (subscribe to WebSocket connection state) is Day 18 polish if at all.

---

## Manual test checklist (run at end of day)

1. **Dev cleanup is complete** — no cycler buttons, no amplitude slider, no ping button, no inline status badge, no "J.A.R.V.I.S — online" text. Source-grep for `import.meta.env.DEV` returns no hits in `App.tsx`.
2. **Header band looks right** — `● JARVIS // MK 0` left, gear + close right. Drag-to-move still works (drag from the header band). Close button still exits.
3. **Bottom status bar** — `● IDLE` shown with `READY · ALT+SPACE` subtext. Waveform bars are calm (3px each, dim). Mic icon is cyan.
4. **PTT cycle status bar transitions** — hold Alt+Space:
   - LISTENING: status text `● LISTENING`, color teal, subtext `MIC OPEN · GROQ STT`, mic icon teal, waveform pulses with your voice.
   - Release → TRANSCRIBING then THINKING: colors shift, subtexts update.
   - SPEAKING: status text `● SPEAKING`, color olive, subtext `TTS · PIPER`, mic icon olive, waveform pulses with TTS.
   - Returns to IDLE.
5. **Mute toggle** — Ctrl+Alt+J → status text `● MUTED`, color gray, subtext `MUTED · CTRL+ALT+J`. Mic icon dimmed. Re-toggle returns to IDLE.
6. **Gear opens settings panel** — click gear icon → panel animates in (~200ms). Click gear again → animates out. Press Escape while open → animates out.
7. **Settings panel sections render** — MIC DEVICE shows current device with dropdown. PROJECT shows current project (`general` if no other) with dropdown. HOTKEY shows the static text.
8. **Mic device change persists** — change selection, restart app, confirm same device is selected.
9. **Project switcher works** — change project, send a voice command, confirm it's logged to the new project (check DB or use voice command "what did we just say"). Restart app, confirm the active project is still set.
10. **Chat panel** — empty initially (panel not rendered). After PTT cycle, chat panel appears bracket-framed with `[icon] CHAT [N RECENT]` header. After 6+ messages, only the last 5 are visible. The `messages` state in App.tsx still holds all of them — only the display is limited.
11. **HUD frame corners** are visible on all four corners of: chat panel, settings panel (when open), status bar. Same cyan tint, consistent size.
12. **CPU baseline check** — idle CPU on WebView under 10%. Record actual value in journal.

---

## Completion criteria

- [ ] All four CL-* cleanup tasks complete; `App.tsx` is visibly shorter
- [ ] `HudFrame` component exists and is used by chat panel, settings panel, and status bar
- [ ] `HeaderBand` component replaces the inline drag bar; both buttons (gear + close) work
- [ ] `StatusBar` component renders all 7 voice states with correct colors and subtexts
- [ ] Waveform bars in status bar respond to amplitude on listening + speaking
- [ ] Lucide React installed and importing cleanly; no console warnings
- [ ] Settings panel hidden by default; gear toggle + Escape close both work
- [ ] Mic device dropdown lists devices and persists across restart
- [ ] `GET /projects` and `POST /projects/active` exist on backend (verify path; build if missing)
- [ ] Project switcher dropdown lists projects, sets active, persists across restart
- [ ] Chat panel is HUD-framed and limited to last 5 messages
- [ ] All 12 manual tests pass
- [ ] CPU under 10% in idle (record in journal)
- [ ] No console errors or warnings
- [ ] You can explain why the gear toggle uses Framer Motion animation rather than a CSS `display` toggle (hint: AnimatePresence handles enter/exit lerp cleanly)

---

## Watch-outs / gotchas

- **Lucide React tree-shaking only works with named imports.** Use `import { Mic, Settings, X } from "lucide-react"`. Default-import patterns bundle all 1000+ icons. Verify the production build size after T-2 — if it's noticeably bigger, the import pattern is wrong.
- **The `HudFrame` corner divs must use `border-style: solid` explicitly.** A bare `border-width: 1px 0 0 1px` without `border-style` renders nothing (browsers default to `none`). Easy 10-minute bug to lose if you forget.
- **`-webkit-app-region: no-drag` must be set on BOTH the gear and close buttons,** not just one. Otherwise clicking the gear gets swallowed by the drag handler.
- **Escape-key listener cleanup is critical.** The `useEffect` returning the `removeEventListener` cleanup is what prevents leaked listeners across settings open/close cycles. Verify this in React DevTools' "highlight updates on re-render" mode if you suspect issues.
- **Framer Motion's `AnimatePresence` requires the conditional to be the direct child.** `{settingsOpen && <SettingsPanel />}` works if `SettingsPanel`'s root is a `<motion.div>`. If you wrap it in something else first, AnimatePresence can't see the mount/unmount and won't animate.
- **The status bar waveform RAF loop needs its own cleanup** on unmount. Same pattern as the blob's RAF loop (returning `cancelAnimationFrame` from the `useEffect`).
- **Don't `position: fixed` the status bar.** PyWebView's window will scroll content if it overflows; `position: fixed` breaks that and creates weird overlap. Use `position: absolute` within a relatively-positioned parent.
- **Project list endpoint should return projects sorted by recency** (last active first, then alphabetical) — most useful default for a dropdown. If you have to add the endpoint, build it this way; if it already exists with a different order, no need to change.
- **`sqlite_store.set_active_project` may not exist by exact name.** Day 5 added project switching but the function name in the architecture skill is a label, not necessarily a verbatim signature. Grep the actual code before assuming. Adjust the new HTTP endpoint to call whatever it's actually called.
- **The `> CHAT` user-message prefix character (`>`) and `<` assistant prefix should be styled as 10px mono with low alpha** so they read as ornamentation, not content. If they're too prominent they compete with the actual message text.
- **Test the gear icon at multiple cursor positions.** Because the drag bar covers the header and the gear lives inside it, the click hitbox must work cleanly. If the gear feels unclickable, `no-drag` is missing or wrong.
- **The voice state palette in the status bar will clash with the orb's palette if you let it.** The colors above were picked to harmonize with the orb's per-state palettes — don't substitute "punchier" colors without checking against the orb side-by-side.

---

## Git commit messages

Three logical commits. The first is housekeeping; the next two are the substantive work.

```
chore: remove dev scaffolding from App.tsx

- Delete import.meta.env.DEV block (state cycler + amplitude slider)
- Delete devAmp / setDevAmp state
- Delete Ping backend button + ping state (Day 3 smoke test)
- Delete inline status badge, "J.A.R.V.I.S — online", debug state label
- Net: App.tsx down ~80 lines, scoped to real app behavior
```

```
feat(ui): hud aesthetic foundation - frame, header band, status bar

Introduces the Image-2-inspired HUD visual language:
- HudFrame component (4 L-shaped corner brackets, reusable)
- HeaderBand replaces ad-hoc drag bar (mono "JARVIS // MK 0" identity,
  gear + close buttons, drag handle preserved)
- StatusBar replaces inline status badge (mic icon, 7 amplitude-driven
  waveform bars, ● [STATE] text with operational subtext per state)
- Per-state palette pulled from orb's existing color language so chrome
  harmonizes with the centerpiece
- Lucide React added as icon library (~12KB tree-shaken)

Cyan/teal accents (#7CD4E8 primary); no red.
```

```
feat: settings panel with gear toggle + project switcher

- SettingsPanel gains HUD treatment: HudFrame, mono uppercase section
  labels, [label] [value] row pattern
- Hidden by default; toggled by gear icon in header band; Escape closes
- Framer Motion enter/exit animation (~200ms)
- New sections: mic device dropdown, project dropdown, hotkey display
- Backend: GET /projects, POST /projects/active endpoints (Path A: existed
  already, just wired / Path B: built fresh on top of sqlite_store)
- Chat panel: HudFrame wrapper, [icon] CHAT [N RECENT] header, last 5
  messages shown (full history preserved in App.tsx state)
```

---

## Time budget

| Phase | Estimate |
|---|---|
| CL-1 → CL-4 cleanup | 40 min |
| T-1 HudFrame | 30 min |
| T-2 Lucide install + verify | 10 min |
| T-3 HeaderBand | 30 min |
| T-4 Settings panel restructure | 1 hour |
| T-5 Project switcher (Path A 15 min / Path B 60 min) | 15–60 min |
| T-6 StatusBar (component + waveform RAF) | 1 hour |
| T-7 ChatPanel HUD treatment | 30 min |
| T-8 App.tsx wiring | 30 min |
| Manual testing | 30 min |
| Buffer / unforeseen | 45 min |
| **Total (Path A)** | **~5.5 hours** |
| **Total (Path B)** | **~6.5 hours** |

Original Day 17 budget was 5 hours. We're slightly over, especially if Path B. If you're tight, the **most droppable item is T-7** (chat panel HUD treatment) — the panel will still work without bracket frames; it just won't match aesthetically. Push to Day 18 if needed.

---

## Workflow reminders

- **Write the prop interfaces yourself first** for every new component (`HudFrame`, `HeaderBand`, `StatusBar`). Read them aloud — if you can't say what each prop is for in one sentence, the interface is wrong.
- **Commit per task, not per day.** The three commits above are a guideline; smaller is fine.
- **Test the dev cleanup before doing anything else.** If `App.tsx` still has dev cruft after CL-1 to CL-3, you'll keep accidentally interacting with it during the day. Get this surgical and verified first.
- **Sanity-check the HUD aesthetic against the mockup** after T-6 lands. The header + bottom status bar are the highest-impact aesthetic changes; if they don't feel right at that point, the rest of the day is downhill.
- **Don't tune more than one HUD parameter at a time** when adjusting visuals (e.g., corner color alpha, letter-spacing, panel background tint). Change one, look, change next. Otherwise you'll lose track of what made it better or worse.

---

## When you get stuck

The likely stuck-points and what to do:

- **Lucide icons render blank:** wrong import pattern. Verify `import { Mic } from "lucide-react"`, not `import Mic from "lucide-react/mic"` or default imports.
- **HudFrame corners don't appear:** `border-style: solid` missing on the corner divs, OR the parent doesn't have `position: relative` so the absolute-positioned corners flee to a grandparent.
- **Settings panel doesn't animate on close:** the `AnimatePresence` is wrapping the wrong thing, OR the panel's root element isn't a `motion.div`. AnimatePresence needs the *direct child* to be a motion component.
- **Drag bar broken after HeaderBand replaces it:** the new band is missing `WebkitAppRegion: "drag"` on the wrapper, OR has `no-drag` on the wrong elements.
- **Gear icon unclickable:** the button needs `WebkitAppRegion: "no-drag"` AND a non-transparent click area. Add `padding: 4px` to give the icon a real hit target.
- **Waveform bars don't move:** `amplitudeRef` not passed to `StatusBar`, OR the RAF loop in `StatusBar` is running but reading the wrong ref (e.g. a fresh one created inside the component rather than the prop).
- **Project list endpoint returns empty:** projects table empty, OR endpoint querying the wrong column. Check `sqlite_store` for the exact query and column names.
- **TS error on the `amplitudeRef` prop:** use the `{ current: number }` pattern per Day 16, not `MutableRefObject<number>`. React 19 marks MutableRefObject deprecated.

If you're stuck for more than 30 minutes on any one thing, paste the symptom + relevant code snippet and ask. Don't grind.
