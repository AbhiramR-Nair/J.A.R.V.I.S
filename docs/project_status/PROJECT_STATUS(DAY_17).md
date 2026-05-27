# Project Status — Day 17

**Period covered:** Day 17 (Week 3, Day 3 — Window Polish + HUD Aesthetic)
**Status:** Complete — all mandatory tasks done, dark background bonus also shipped.
**Environment:** Windows 11, Python 3.13.5, FastAPI, React 19 + Vite, Framer Motion 12.40, Lucide React, Groq Whisper-large-v3, Piper `en_GB-alan-medium`, Gemini 2.5 Flash

> Checkpoint summary for Day 17: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 18.

---

## 1. What has been done

Day 17 stripped all dev scaffolding from `App.tsx` and replaced ad-hoc placeholder UI with a
coherent HUD visual language — bracket-framed panels, mono uppercase labels, a characterful
header band, and an instrument-like bottom voice status bar. The project switcher was built
from scratch (Path B) on both the backend and frontend.

| Task | What landed | Status |
|---|---|---|
| CL-1 — Remove dev cycler + slider | Deleted `import.meta.env.DEV` block (state cycler + amplitude slider) and `devAmp`/`setDevAmp` state. | Done |
| CL-2 — Remove status badge + debug labels | Removed `statusLabel` computation, `Status: {statusLabel}` badge, lowercase voice state debug label, `J.A.R.V.I.S — online` div, and the `transcribing`/`lastRecording` state + their `useEffect` timers. | Done |
| CL-3 — Remove Ping backend button | Deleted `pingBackend()` function, `ping` state, the button and result display JSX. `API_BASE` import kept because `closeApp` still uses it. | Done |
| T-1 — `HudFrame` component | New `frontend/src/components/HudFrame.tsx`. Four absolutely-positioned L-shaped corner divs. Props: `cornerColor`, `padding`, `background`, `className`. Used by StatusBar, ChatPanel, SettingsPanel. | Done |
| T-2 — Install Lucide React | `npm install lucide-react`. Named imports confirmed for `Mic`, `Settings`, `X`, `MessageSquare`, `ChevronDown`. | Done |
| T-3 — `HeaderBand` component | New `frontend/src/components/HeaderBand.tsx`. Replaces the old drag bar. `● JARVIS // MK 0` left, gear + close right. Full bar is `WebkitAppRegion: drag`; both buttons override with `no-drag`. `statusDotColor` prop forward-prepared. | Done |
| T-4 — Settings panel restructure | `SettingsPanel.tsx` fully rewritten. Root is a `motion.div` for `AnimatePresence` exit animation. `HudFrame` wrapper. Sections: MIC DEVICE (fetches `GET /audio/devices` + `GET /audio/device` on mount), PROJECT (T-5), HOTKEY (static). Mic test button restyled. Escape key closes via `App.tsx` effect. | Done |
| T-5 — Project switcher (Path B) | New `backend/models/projects.py` (`ProjectInfo`, `SetActiveProjectRequest`). New `backend/api/projects.py` (`GET /projects`, `POST /projects/active`). Registered in `backend/main.py`. `SettingsPanel.tsx` project dropdown wired: fetches on mount, `POST` on change, optimistic update. | Done |
| T-6 — `StatusBar` component | New `frontend/src/components/StatusBar.tsx`. `HudFrame` wrapper, `Mic` icon, 7 waveform bars with RAF loop (center-weighted bell curve, min 3px, +11px at amplitude 1.0), per-state accent color + subtext for all 7 voice states. | Done |
| T-7 — Chat panel HUD treatment | `ChatPanel.tsx` rewritten. `HudFrame` wrapper, `[MessageSquare] CHAT [N RECENT]` header, last 5 messages displayed (full history preserved in App state), `>` / `<` prefix characters, per-role color differentiation. | Done |
| T-8 — Wire up in `App.tsx` | `StatusBar` imported and placed `absolute bottom-3 left-3 right-3`. Content area gains `pb-20` + `overflow-y-auto`. `HeaderBand` replaces old drag bar. `AnimatePresence` wraps settings conditional. Escape key effect wired. | Done |
| Bonus — Dark background | `#060d14` on `#root` in `index.css` (prevents white flash before mount) and on App root `div` (runtime coverage). `html`/`body` stay `transparent` for PyWebView overlay. | Done |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. `motion.div` as SettingsPanel root — why it matters for AnimatePresence

`AnimatePresence` animates children on mount and unmount. For the exit animation to fire,
`AnimatePresence` must be the direct parent of the conditional, and the conditional's root
element must be a `motion.*` component. If `SettingsPanel` returned a plain `div`, the panel
would vanish instantly on close — no fade, no slide. Making the root a `motion.div` with
`initial/animate/exit` props is the one-line fix that makes the whole enter/exit lerp work.

### 2. Project switcher as Path B — why no endpoints existed

The architecture plan referenced `sqlite_store.set_active_project` and `list_projects` as
existing functions (they were built on Day 5). However, no HTTP layer had been wired above
them — the voice loop calls `get_active_project()` directly at the Python level, bypassing
the API entirely. The frontend had no way to read or change the active project without new
endpoints. Two thin wrapper endpoints were added in `backend/api/projects.py`, keeping the
HTTP layer as a simple translator from request body → store function → Pydantic response.
No business logic was added to the API layer.

### 3. `voiceStateRef` pattern in StatusBar RAF loop

The StatusBar's RAF loop needs to know the current `voiceState` to decide whether to animate
the waveform bars. A naive approach — putting `voiceState` in the `useEffect` dependency
array — would tear down and recreate the RAF loop on every state transition. Instead, the
latest `voiceState` is mirrored into a `ref` (`voiceStateRef.current = voiceState`) on every
render, and the RAF closure reads `voiceStateRef.current`. The loop runs once for the
component's lifetime and always sees the current state without being recreated. Same pattern
as the blob's rAF loop from Day 15.

### 4. `HudFrame` corner border trick

The four L-shaped corners are four `div`s absolutely positioned at each corner. Each div has
`borderStyle: "solid"` set on the shared `CORNER_BASE` constant, then overrides only the two
sides that form its L (e.g., `borderTop` + `borderLeft` for the top-left corner) — the other
two sides inherit `borderWidth: 0` from the base so they're invisible. The `borderStyle: "solid"`
line is critical: browsers default `border-style` to `none`, so omitting it renders nothing
regardless of `borderWidth`. A 10-minute invisible-corner bug is avoided by keeping this
explicitly in the shared constant.

### 5. Background color approach — `#root` + App div, not `body`

PyWebView's transparent overlay window depends on `body { background: transparent }`. Putting
the dark background on `body` would break the frameless window rendering on Windows. Instead,
`#root` gets `background: #060d14` in `index.css` to prevent the white WebView2 chrome from
flashing before React mounts, and the App root `div` carries the same color at runtime.
`html` and `body` stay transparent throughout. This pattern keeps the PyWebView overlay
semantics intact while giving the app a painted dark background.

### 6. Optimistic update for device and project persistence

Both the mic device dropdown and the project dropdown update local state immediately on
change, then fire the `POST` in the background. If the `POST` fails (e.g., network hiccup),
the dropdown shows the user's intended selection but the backend didn't persist it — on next
open it will revert to the last saved value. This is acceptable for a personal-use app where
the backend is always localhost; the UX is snappier than waiting for confirmation before
updating the dropdown.

---

## 3. Problems faced and how they were handled

### Problem 1 — Cascade of TypeScript errors during cleanup

**What happened:** CL-2 removed the `lastRecording`, `setLastRecording`, `transcribing`, and
`setTranscribing` state declarations in one edit. This immediately produced TS errors for
every remaining reference to those names in the event handler `useEffect` and the `statusLabel`
computation — all of which were removed in subsequent edits. The intermediate error state was
expected but noisy in the IDE diagnostics panel.

**Fix:** Made the edits in a deliberate sequence — declarations first, then references in
logic, then the `useEffect` cleanup, then `statusLabel`, then the JSX elements. Each step
left one layer of dangling references that was cleaned up in the very next edit. No errors
survived to the final state.

**Rule going forward:** When removing state that has multiple callsites, plan the removal
order: declaration → logic callsites → derived values → JSX. This minimises the window where
the file is in an unbuildable state.

### Problem 2 — `"break-words"` is not a valid CSS `WordBreak` value

**What happened:** `ChatPanel.tsx` used `wordBreak: "break-words"` as an inline style. TypeScript
flagged this as a type error: `Type '"break-words"' is not assignable to type 'WordBreak'`.
The confusion is that `break-words` is a **Tailwind utility class name** (maps to
`overflow-wrap: break-word`), not a valid CSS `word-break` property value.

**Fix:** Changed to `wordBreak: "break-word"` — the correct CSS property value.

**Rule going forward:** When converting Tailwind class names to inline `style` objects, check
the actual CSS property value rather than copying the Tailwind utility name. They often differ
(e.g., `break-words` → `word-break: break-word`; `truncate` → multiple properties).

### Problem 3 — Project switcher was Path B, not Path A

**What happened:** The plan flagged T-5 as an unknown — Path A if endpoints existed (~15 min),
Path B if not (~60 min). Grepping `backend/api/` confirmed no `/projects` endpoints existed;
the sqlite functions were only called directly from Python. Path B was taken.

**Fix:** Built `backend/models/projects.py` and `backend/api/projects.py` from scratch as thin
wrappers over the existing store functions. Registered the router in `main.py`. Total time was
close to the Path B estimate.

**No downstream risk** — the store functions (`list_projects`, `set_active_project`,
`get_active_project`) were already tested implicitly by the voice loop using them since Day 5.
The new HTTP layer just exposes them.

---

## 4. Heads-up: downstream complications to watch

### `AnimatePresence` key requirement if multiple panels are added

`AnimatePresence` currently wraps a single `{settingsOpen && <SettingsPanel .../>}`. If a
second overlay (e.g., a project-detail panel) is added later inside the same `AnimatePresence`,
each conditional child will need a unique `key` prop. Without it, React cannot distinguish
between two different components mounting/unmounting and the exit animation may not fire
correctly for the outgoing one.

**Mitigation:** When adding a second `AnimatePresence` child, add `key="settings"` to
`SettingsPanel` and `key="<name>"` to the new panel at that point.

### StatusBar RAF loop runs even when voice is idle

The StatusBar's RAF loop runs at 60fps for the lifetime of the component (i.e., always, since
it's always rendered). In idle/muted/thinking/error states, it still iterates through 7 bar
elements every frame — but only writes `3px` height, which is the same value as the initial
style. Modern browsers are good at skipping no-op style writes, but it's still 60 RAF ticks
per second doing work.

**Mitigation if CPU becomes an issue:** Add a check at the top of `tick()` — if
`amplitudeRef.current === 0` and `voiceStateRef.current` is not `listening` or `speaking`,
skip the bar writes entirely and reschedule. Not worth adding now.

### Project dropdown doesn't reflect external project changes

The project dropdown fetches the project list once on `SettingsPanel` mount. If the active
project is changed via a voice command (e.g., "switch to fitness project") while settings is
open, the dropdown will show the stale selection until the panel is closed and reopened.

**Mitigation:** For v1, this is acceptable. In Week 4 (Day 21), when project memory tools
are built, a WebSocket `project_changed` event could be broadcast and `App.tsx` could close
and reopen the settings panel, or the panel could re-fetch on a relevant event.

### `tts_calibration_max` still untuned

Carried over from Day 16. If TTS amplitude reactivity looks wrong (pinned at 1.0 or near 0
throughout a response), adjust `tts_calibration_max` in `.env`. Target range for `latest_amplitude`
during normal speech: 0.2–0.8. The StatusBar waveform will also reflect this calibration
since it shares the same `amplitudeRef`.

---

## 5. How to verify Day 17

```
1. Dev cleanup
   grep -r "import.meta.env.DEV" frontend/src/  →  no results
   grep -r "pingBackend" frontend/src/           →  no results
   grep -r "statusLabel" frontend/src/           →  no results

2. Header band
   App renders "● JARVIS // MK 0" left, gear + close right.
   Drag from the header band → window moves.
   Close button → app exits.

3. Bottom status bar
   Idle: "● IDLE", "READY · ALT+SPACE", cyan, waveform bars flat at 3px.

4. PTT cycle through states
   Hold Alt+Space → LISTENING (teal, "MIC OPEN · GROQ STT", bars pulse with voice).
   Release → TRANSCRIBING then THINKING (blue, "GEMINI 2.5 FLASH").
   Response plays → SPEAKING (olive, "TTS · PIPER", bars pulse with TTS).
   Finishes → IDLE.

5. Mute toggle
   Ctrl+Alt+J → MUTED, gray, "MUTED · CTRL+ALT+J".
   Re-toggle → IDLE.

6. Gear toggle + settings panel
   Click gear → panel fades in (~200ms). Click gear again → fades out.
   Press Escape while open → fades out.

7. Settings panel sections
   MIC DEVICE dropdown lists system input devices.
   PROJECT dropdown lists projects (at minimum "general").
   HOTKEY shows "ALT + SPACE".

8. Project switcher persistence
   Change project to a new name → restart app → project is still active.

9. Chat panel
   Empty initially (panel hidden). After PTT turn, panel appears with HudFrame
   brackets, "[icon] CHAT [N RECENT]" header, messages with > / < prefixes.
   After 6+ messages, only last 5 visible.

10. HUD corner brackets
    Visible on all four corners of: chat panel, settings panel, status bar.

11. Dark background
    App background is dark blue-black (#060d14). No white flash on load.

12. CPU baseline
    Idle CPU on WebView2 process under 10%.
```

All checks passed on 2026-05-28.

---

## 6. Open items before Day 18

- [ ] Snap-to-corner drag behavior (Day 18)
- [ ] Minimize to system tray (Day 18, requires `pystray`)
- [ ] Tune `tts_calibration_max` if TTS waveform reactivity feels wrong
- [ ] Tag `v0.3.0-blob` at end of Day 19

---

## 7. Files changed this day

```
NEW:
  frontend/src/components/HudFrame.tsx         — reusable L-bracket corner frame
  frontend/src/components/HeaderBand.tsx        — drag bar replacement with identity + buttons
  frontend/src/components/StatusBar.tsx         — voice status bar with waveform + per-state palette
  backend/api/projects.py                       — GET /projects, POST /projects/active
  backend/models/projects.py                    — ProjectInfo, SetActiveProjectRequest

EDIT:
  frontend/src/App.tsx                          — cleanup (~80 lines removed), HeaderBand,
                                                  AnimatePresence, settingsOpen state, Escape
                                                  effect, StatusBar placement, dark bg
  frontend/src/components/SettingsPanel.tsx     — full rewrite: motion.div, HudFrame, device
                                                  dropdown, project dropdown, HUD row pattern
  frontend/src/components/ChatPanel.tsx         — HudFrame, header row, last-5 slice, prefixes
  frontend/src/index.css                        — #root background #060d14
  backend/main.py                               — projects router import + include_router
  frontend/package.json                         — lucide-react added
```

---

## 8. Commits

```
(pending)
```
