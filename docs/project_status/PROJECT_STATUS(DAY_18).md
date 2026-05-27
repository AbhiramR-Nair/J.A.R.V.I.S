# Project Status — Day 18

**Period covered:** Day 18 (Week 3, Day 4 — Window Polish: Snap, Tray, Connection Dot)
**Status:** Complete — all mandatory tasks done, all 17 manual tests passing.
**Environment:** Windows 11, Python 3.13.5, PyWebView 6.2.1, pystray 0.19.5, Pillow 12.2.0, React 19 + Vite, Framer Motion 12.40, Groq Whisper-large-v3, Piper `en_GB-alan-medium`, Gemini 2.5 Flash

> Checkpoint summary for Day 18: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 19.

---

## 1. What has been done

Day 18 closed the two pre-flight carryovers from Day 17 and shipped three substantive window-behaviour features, making the overlay feel like a real desktop daily-driver rather than a dev artefact.

| Task | What landed | Status |
|---|---|---|
| PF-1 — TTS calibration check | `tts_calibration_max = 0.3` confirmed acceptable. Waveform bars pulse visibly with speech rhythm (30–80% of max height). Locked. | Done |
| PF-2 — StatusBar RAF idle guard | 8-line guard added at the top of `tick()` in `StatusBar.tsx`. When `amplitudeRef === 0` AND voice state is not `listening` or `speaking`, the RAF loop skips all per-frame bar writes and reschedules. Loop lifetime unchanged — picks up next real state transition immediately. | Done |
| T-1 — Snap-to-corner | New `backend/desktop/snap.py` with `SnapManager`. 150ms debounce on `window.events.moved` (fires continuously during drag in PyWebView 6.x / WinForms, not just at drag-end). Snaps to nearest of 4 corner targets if center-to-center distance ≤ 60px. 8px margin from screen edge. Atomic write-rename persistence to `data/window_state.json`. `restore_position()` bound to `window.events.shown` for clean startup restore. 500ms cooldown prevents snap→move→moved→snap loops. | Done |
| T-2 — Minimize-to-tray | New `backend/desktop/tray.py` with `TrayManager`. pystray 0.19.5 + Pillow 12.2.0 installed. 64×64 cyan circle PNG generated at `assets/tray_icon.png`. Tray icon appears on launch; Show / Quit menu; single-click calls Show. Runs on a daemon thread so PyWebView keeps the main thread. | Done |
| T-3 — Window-hide endpoint | New `backend/api/window.py` with `POST /window/hide`. Frontend `closeApp()` in `App.tsx` changed from `/shutdown` to `/window/hide` — X button now hides to tray instead of killing the process. Real quit is tray-only. | Done |
| T-4 — Connection-state dot | `useWebSocket.ts` exports `ConnectionState` type and `connectionState` state. Retry counter ref (`retryCountRef`) increments on each `onclose`, resets on `onopen`. After 5 consecutive failures: `reconnecting` → `disconnected`. `HeaderBand.tsx` replaced `statusDotColor?: string` prop with `connectionState: ConnectionState`; maps internally via `DOT_COLORS` record. Amber CSS pulse animation (`dot-reconnecting` class, `@keyframes dot-pulse` in `index.css`) during reconnecting. `App.tsx` threads `connectionState` through. | Done |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. Debounced snap instead of drag-end event

The plan assumed `window.events.moved` fires only when the user finishes dragging (drag-end). Checking PyWebView 6.2.1 source (`winforms.py:306 — self.Move += self.on_move`) reveals it is wired to WinForms `Form.Move`, which fires on every pixel change during a drag — continuously, not just at release.

Calling `window.move()` to snap on every pixel would cause a fight between the user's drag and the snap code. The fix is a **150ms debounce**: `on_moved` restarts a `threading.Timer` on each event. The timer fires once the window has been still for 150ms — reliably indicating drag-end. This is invisible to users (a 150ms post-release delay before the snap animation is imperceptible) and handles the continuous-event reality correctly.

### 2. Snap cooldown to prevent recursive moved events

`window.move()` (our programmatic snap call) also triggers `on_moved` events because PyWebView's WinForms `Form.Move` fires for all position changes including programmatic ones. Without a guard, `_apply_snap` → `window.move()` → `on_moved` → restart debounce timer → `_apply_snap` → infinite loop.

Fix: `_cooldown_until` timestamp. After snapping, `_cooldown_until = time.monotonic() + 0.5`. Any `on_moved` that fires before that timestamp is silently dropped. The 500ms window is large enough to absorb any programmatic-move events (which arrive within a few milliseconds) without blocking real user drags.

### 3. Atomic write-rename for window state persistence

`data/window_state.json` is written on every drag-end and every snap. If the app crashes mid-write (power cut, exception, force-kill), a direct `file.write_text(json.dumps(...))` can leave a truncated file. On next launch, `json.loads()` would fail and the position would not restore.

Fix: write to a `.tmp` file first, then `tmp.replace(state_path)`. On POSIX this is atomic; on Windows it's near-atomic (Win32 `MoveFileEx` with `MOVEFILE_REPLACE_EXISTING`). A crash between the two steps leaves either the old JSON intact (if the crash was before the rename) or the new JSON intact (after) — never a truncated file.

### 4. pystray on a daemon thread, PyWebView on the main thread

Both pystray (`pystray.Icon.run()`) and PyWebView (`webview.start()`) block their calling thread and run their own event/message loops. On Windows, PyWebView uses WinForms which must own the main thread. pystray on Windows uses a Win32 message loop that can run on any thread.

The only safe arrangement: PyWebView on main thread, pystray on `threading.Thread(target=icon.run, daemon=True)`. `daemon=True` is non-negotiable — without it the pystray thread outlives the main thread after `window.destroy()` and leaves a zombie icon in the system tray.

### 5. Singleton storage in `backend/desktop/tray.py`, not in `__main__.py`

The `POST /window/hide` endpoint needs to reach the `TrayManager` instance. The naive approach (store it in `__main__.py` as `_tray`) hit a **Python module identity bug**: when you run `python -m backend.desktop`, the launcher runs as `sys.modules['__main__']`, not as `sys.modules['backend.desktop.__main__']`. When the endpoint later does `from backend.desktop.__main__ import get_tray`, Python imports a *fresh copy* of the module (registered under `'backend.desktop.__main__'`) with `_tray = None`. The real tray instance — held in `sys.modules['__main__']` — is unreachable this way.

Fix: store the singleton in `backend/desktop/tray.py` via `set_instance()` / `get_instance()`. This module is always imported as `backend.desktop.tray` regardless of which file calls it, so all importers see the same object and the same `_instance`. `__main__.py` calls `set_tray(tray)` before `webview.start()`; the endpoint calls `get_instance()` — same module, same value.

### 6. `ConnectionState` retry counter as a ref, not state

The retry counter (`retryCountRef`) that tracks how many consecutive WebSocket failures have occurred is stored as a `useRef`, not `useState`. It drives no render — only `connectionState` (which is `useState`) triggers the dot color update. Using `useRef` avoids a spurious re-render on every failed reconnect attempt and correctly reads the latest count inside the `useEffect` closure without needing to be in the dependency array.

---

## 3. Problems faced and how they were handled

### Problem 1 — `window.events.moved` fires continuously during drag, not just at drag-end

**What happened:** The Day 18 plan assumed PyWebView's `moved` event fires once when the user releases the mouse (drag-end), similar to Windows's `WM_EXITSIZEMOVE`. Checking the installed PyWebView 6.2.1 source (`winforms.py`) showed it wires `Form.Move += on_move`, which fires on every pixel change — potentially hundreds of times during a single drag.

**Impact:** Calling `window.move()` to snap on every event would create a constant fight between the user's drag and the snap code, making the window behave erratically.

**Fix:** 150ms debounce timer, restarted on each `on_moved` call. Fires once after the window has been still for 150ms. See §2.1 above.

**Rule going forward:** Always verify the exact firing semantics of PyWebView events against the installed source (`site-packages/webview/platforms/winforms.py`) before assuming plan-document descriptions are accurate. PyWebView's event docs lag behind the implementation.

### Problem 2 — `get_tray()` from `__main__.py` always returned `None`

**What happened:** After T-2 and T-3 were implemented, clicking X did not hide the window — the app continued to close completely. Adding logging confirmed `/window/hide` was reached but returned `{"status": "no_shell"}`. `get_tray()` from `backend.desktop.__main__` was returning `None` even though `_tray` was set in `main()`.

**Root cause:** `python -m backend.desktop` registers the launcher as `sys.modules['__main__']`, not `sys.modules['backend.desktop.__main__']`. When the endpoint imported `from backend.desktop.__main__ import get_tray`, Python loaded a second, independent copy of `__main__.py` as a new module object. That copy had `_tray = None` (module-level initialisation), never touched by the real `main()` running in `sys.modules['__main__']`.

**Fix:** Moved the singleton storage to `backend/desktop/tray.py` — a module that is always imported as `backend.desktop.tray` by all callers. `set_instance()` / `get_instance()` functions make the reference explicitly available without relying on a module-global that could be split across two identities.

**Rule going forward:** Never store cross-module singleton state in `__main__.py` files. Python's `-m` execution mechanism makes `__main__` files a special case where the module identity is `__main__`, not the dotted package path. Always use a non-`__main__` sibling module as the canonical singleton home.

---

## 4. Heads-up: downstream complications to watch

### Snap targets are computed for a fixed window size (400×600)

`SnapManager` hard-codes `_WINDOW_W = 400` and `_WINDOW_H = 600` to compute corner targets and window center distances. If the window size changes (e.g., a future settings panel that expands the window), the snap targets will be wrong — the snapped window will clip off-screen by the size difference.

**Mitigation:** If window dimensions ever change, update `_WINDOW_W` / `_WINDOW_H` in `snap.py` to match. Long-term: read `window.width` / `window.height` dynamically, but PyWebView's `width`/`height` properties require `@_shown_call` and are slightly more complex to plumb in.

### Snap is single-monitor only

`SnapManager` computes targets from `screen_w` / `screen_h` passed at construction (from the primary monitor via tkinter). If the user moves the overlay to a secondary monitor and drags to a corner there, the snap targets will still be for the primary monitor — the window may snap to a position off-screen on the secondary display.

**Mitigation:** Acceptable for Day 18 (documented in the plan as out of scope). If multi-monitor support matters before Day 30, `SnapManager.on_moved` could re-query screen dimensions, but this requires a way to identify which monitor the window is currently on (which PyWebView 6.x does not expose directly).

### Hidden window still runs the RAF loops and WebSocket

When the window is hidden via `window.hide()`, the React app keeps running inside the WebView2 process. The StatusBar RAF loop, the Blob RAF loop, and the WebSocket reconnect loop all continue ticking at full rate. This is the correct behaviour for keeping the voice loop alive, but it means CPU usage while hidden is essentially the same as while visible.

**Mitigation:** Not a concern for now — the PF-2 RAF guard already eliminates most idle ticks. If CPU while hidden becomes measurable, a future polish day could post a `window_hidden` WebSocket event from the endpoint and pause the RAF loops client-side.

### `window.events.shown` fires on every show, not just on first launch

`restore_position()` is bound to `window.events.shown`. This event fires every time `window.show()` is called — including when the user shows the window from the tray. Each show triggers a `window.move()` call, which sets a cooldown and triggers another `moved` event (suppressed by the cooldown). In practice this is harmless (the window moves to its saved position, which is usually where it already is), but it does cause a brief invisible position "snap" on every show.

**Mitigation:** If this ever causes a visible jump, gate `restore_position` behind a `_restored` flag that flips to `True` after the first call and short-circuits subsequent invocations.

### pystray tray icon does not survive system tray overflow

On Windows, if the notification area is full, pystray icons can be hidden behind the "Show hidden icons" caret rather than appearing directly in the visible tray. The icon is still functional (right-click, single-click work from the overflow panel), but users may not notice it immediately.

**Mitigation:** On first hide, consider showing a Windows toast notification ("J.A.R.V.I.S is running in the system tray") via `plyer` (already a dependency from the timer tool). Deferred to Day 26 when plyer is wired for timers anyway.

### Connection dot stays amber during normal startup (~200ms)

`connectionState` initialises to `"connecting"` (amber). The WebSocket typically connects within 200ms of the React app mounting, so the dot flashes amber briefly on every launch before turning cyan. This is cosmetically unideal but harmless.

**Mitigation:** Acceptable for now. If it becomes visually distracting, gate the amber render behind a 300ms `setTimeout` in `HeaderBand` — only show non-cyan colors after a short grace period.

---

## 5. How to verify Day 18

```
Pre-flight
  1. TTS calibration: ask a 2-3 sentence question; StatusBar bars pulse
     visibly (30–80% max height) with speech rhythm. Value: 0.3. [confirmed]

  2. StatusBar RAF idle guard: open DevTools → Performance → record 5s at idle.
     No per-frame style mutations on the waveform bar elements.

Snap-to-corner
  3. Drag window near each of 4 corners; release within 60px → snaps to corner.
  4. Drag to screen centre; release → stays where dropped (no snap).
  5. Snap to top-right, fully quit (tray → Quit), relaunch → window appears
     at top-right.
  6. Drag to middle, quit, relaunch → window appears in the middle.

Tray
  7. Cyan dot icon visible in system tray on launch.
  8. Click X → window disappears; tray icon remains; backend still running
     (Alt+Space responds with voice).
  9. Single-click tray icon → window reappears at same position.
  10. Right-click tray → Show → same effect as single-click.
  11. Right-click tray → Quit → window closes, tray icon disappears,
      no python.exe or msedgewebview2.exe in Task Manager.

Connection dot
  12. Fresh launch with backend running → dot is cyan.
  13. Kill backend (Task Manager → end python.exe) while frontend is open
      → dot turns amber within 1s.
  14. Leave backend killed; after ~5s (5 retries) → dot turns red.
  15. Restart backend → dot returns to cyan once WS reconnects.

Voice loop regression
  16. Full PTT cycle still works: hold Alt+Space → ask question → get spoken
      response. All 7 voice states cycle correctly.
  17. CPU at idle under 10% (record actual value in journal).
```

All 17 checks passed on 2026-05-28.

---

## 6. Open items before Day 19

- [ ] Tag `v0.3.0-blob` (end of Day 19)
- [ ] Day 19 is buffer / light day — start Day 20 (tool-calling architecture) early if time allows
- [ ] Optional: nicer tray icon (J monogram, bracket frame) — Day 30 polish
- [ ] Optional: `window_hidden` WebSocket event to pause RAF loops while hidden — only if CPU while hidden becomes measurable

---

## 7. Files changed this day

```
NEW:
  backend/desktop/snap.py          — SnapManager: debounced snap, atomic persistence
  backend/desktop/tray.py          — TrayManager: pystray daemon thread, singleton storage
  backend/api/window.py            — POST /window/hide endpoint
  assets/tray_icon.png             — 64×64 cyan circle tray icon (Pillow-generated)
  docs/plans/day_18_plan.md        — Day 18 plan document

EDIT:
  backend/desktop/__main__.py      — SnapManager + TrayManager wired; window assigned to var
  backend/main.py                  — window router registered
  backend/requirements.txt         — pystray==0.19.5, pillow==12.2.0 added
  frontend/src/App.tsx             — connectionState threaded to HeaderBand; closeApp → /window/hide
  frontend/src/components/HeaderBand.tsx  — connectionState prop replaces statusDotColor;
                                            DOT_COLORS map; dot-reconnecting pulse class
  frontend/src/components/StatusBar.tsx   — RAF idle guard (skip writes when amp=0 + not audio)
  frontend/src/hooks/useWebSocket.ts      — ConnectionState type; retry counter; onopen handler;
                                            connectionState state; updated onclose
  frontend/src/index.css           — @keyframes dot-pulse + .dot-reconnecting class
```

---

## 8. Commits

```
feat(desktop): snap-to-corner with position persistence
feat(desktop): minimize-to-tray via pystray
feat(ui): connection-state dot in header band + StatusBar RAF idle guard
docs: Day 18 project status document
```
