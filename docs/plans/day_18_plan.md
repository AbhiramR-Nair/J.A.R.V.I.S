# Day 18 Plan — Window Polish: Snap, Tray, Connection Dot

**Week 3, Day 4.** First of the two designated buffer days at the tail of Week 3. The original Day-by-Day plan calls these days "carry forward or finish anything rough." Day 18 closes out the deferred Day-17 polish items so Day 19 can be light (demo, tag `v0.3.0-blob`, optionally start Day 20).

---

## Goal for the day

Three substantive features + two small carryover cleanups, all in service of the same idea: the assistant window should behave like a real desktop daily-driver, not a dev artifact.

1. **Snap-to-corner** drag behavior — the window resolves to one of four corner positions when released near one
2. **Minimize-to-tray** — the X button hides the window into a system tray icon; Quit only via tray menu
3. **Connection-state dot** — header band's status dot reflects WebSocket connection health (cyan/amber/red)
4. **StatusBar RAF idle optimization** — skip writes when nothing's animating
5. **TTS calibration sanity check** — open since Day 16; close it today

By end of day: window position persists across restarts, the assistant keeps running in the tray when "closed," and a network blip shows up as a colored dot instead of a silent broken app.

---

## Locked specs from design discussion

| Decision | Value |
|---|---|
| Snap threshold | **60px** (window center to nearest snap-target center) |
| Snap positions | **4 corners only** — no edge midpoints |
| Snap animation | **Instant** — no easing (avoids flicker on transparent always-on-top window) |
| Snap persistence | **Yes** — save last position to `data/window_state.json`; restore on launch |
| Tray X behavior | **Hide window** — keeps backend + hotkey listener running |
| Tray menu | **Show / Quit** — minimal |
| Hard quit path | **Tray → Quit** — calls existing `/shutdown` |
| Connection dot colors | Cyan `#7CD4E8` connected · Amber `#EF9F27` reconnecting · Red `#E24B4A` disconnected |
| Disconnected threshold | After **5 consecutive reconnect failures**, transition `reconnecting → disconnected` |

---

## Pre-flight (~30 min)

### PF-1. TTS calibration check

**File:** `backend/config/settings.py` (and `.env` if needed)

Open item since Day 16. Quick procedure:

1. Run the app, ask any question that returns 2+ sentences.
2. Watch the StatusBar waveform while TTS plays.
3. Observe the bars:
   - **Pinned to maximum height the whole time** → `tts_calibration_max` too low. Try doubling.
   - **Stay near minimum throughout** → too high. Try halving.
   - **Pulse visibly with syllables, vary 30%–80% of max height** → good. Leave it.

Time-box: 15 min. If the calibration looks acceptable, journal a note that it's locked at `<value>` and move on. If it's clearly broken, fix; if borderline, defer to next polish day.

### PF-2. StatusBar RAF idle optimization

**File:** `frontend/src/components/StatusBar.tsx`

Day 17 status doc flagged: the RAF loop iterates through 7 bar elements every frame even when nothing's animating (idle, muted, thinking, error). Browsers optimize no-op style writes, but 60 RAF ticks/sec doing the loop is still measurable.

Add a guard at the top of `tick()`:

```ts
// Skip the per-frame bar writes when there's nothing to react to. The amplitudeRef
// hits zero on state transitions out of listening/speaking (set by App.tsx in Day 16),
// so this guard fires the moment the visual would settle. RAF continues so we
// pick up the next state transition immediately.
if (
  amplitudeRef.current === 0 &&
  voiceStateRef.current !== 'listening' &&
  voiceStateRef.current !== 'speaking'
) {
  rafId = requestAnimationFrame(tick);
  return;
}
```

Verify in DevTools' Performance tab: an idle 5-second recording should show no per-frame style writes in the StatusBar component.

Time-box: 15 min including verification.

---

## Tasks

### T-1. Snap-to-corner — backend (PyWebView)

**File preflight (5 min, before any code):**

PyWebView version + events API matter here.

```bash
python -c "import webview; print(webview.__version__)"
```

The plan assumes PyWebView **4.x** with `window.events.moved` available. If the version is older or the events API is missing, the fallback is polling `window.x`/`window.y` every 100ms via `asyncio.create_task` — works but uglier. Verify before committing to the events approach.

**Also check for the desktop module/package conflict.** The architecture skill lists both `backend/desktop.py` (PyWebView launcher) and `backend/desktop/` (with `hotkeys.py`). Python can't have a `desktop.py` and `desktop/` package as siblings — one shadows the other. If your actual repo has resolved this (e.g., launcher renamed or merged into the package), good. If both exist, you'll need to consolidate first. Recommended fix: rename `desktop.py` → `backend/desktop/launcher.py`, update the import in wherever launches it (likely `backend/main.py` or a script).

**File:** `backend/desktop/snap.py` (new)

A small module exposing two functions and one state struct:

```python
# Pseudocode signatures — write these yourself first, then ask for implementation.

@dataclass
class WindowState:
    x: int
    y: int
    snapped_corner: Optional[str]  # 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right' | None

class SnapManager:
    """Listens for window-move events, snaps to nearest corner if close enough,
    persists last position to data/window_state.json."""

    def __init__(self, window: webview.Window, screen: webview.Screen,
                 state_path: Path, threshold: int = 60):
        ...

    def on_moved(self, x: int, y: int) -> None:
        """Called by PyWebView when the window finishes moving.
        Computes nearest corner, snaps if within threshold, persists state."""
        ...

    def restore_position(self) -> None:
        """Read data/window_state.json on launch; move window to last known position."""
        ...
```

**Key implementation details:**

- The snap targets are computed once at construction time (from `screen.width`, `screen.height`, and the window's known size). Recompute if the user moves the window to a different monitor — but Day 18 ignores multi-monitor; document as a Day 19 polish if needed.
- Margin from screen edge: **8px**. So `top-left` is `(8, 8)` not `(0, 0)`. Matches how Windows positions its own snapped windows.
- Distance comparison uses **window-center to target-center**, not top-left to top-left. More intuitive — "I dropped the middle of the window near the corner" wins regardless of window size.
- `window.move(x, y)` is the snap-to-corner call. Instantaneous; no animation per spec.
- Persistence: write to `data/window_state.json` after every snap or release. Tiny file, single JSON object. Read on startup via `restore_position()`.

**Wiring in `backend/desktop/launcher.py`** (or wherever PyWebView is launched):

```python
window = webview.create_window(...)
screen = webview.screens[0]  # primary monitor
snap_manager = SnapManager(window, screen, settings.data_dir / 'window_state.json')

window.events.moved += snap_manager.on_moved
window.events.shown += snap_manager.restore_position  # restore after window is ready
```

`window.events.moved` fires when the window finishes a move operation (drag end). This is the right hook — not `loaded`, not `shown`.

### T-2. Minimize-to-tray — backend (pystray)

**File:** `backend/desktop/tray.py` (new)

`pystray` is a small cross-platform tray icon library. On Windows, it uses Win32 API directly. It has its own event loop, so we run it on a daemon thread.

**Install:**

```bash
pip install pystray pillow
# pillow is required by pystray for icon loading
```

Append to `backend/requirements.txt`.

**Icon asset:**

You'll need a 64×64 PNG with transparency. The simplest acceptable version: a solid cyan circle on transparent background. Save as `assets/tray_icon.png` (create the `assets/` directory if it doesn't exist). Path stored in `settings.tray_icon_path`.

For a quick first version, you can generate one with Pillow in a 10-line script:

```python
# scripts/generate_tray_icon.py
from PIL import Image, ImageDraw
img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill='#7CD4E8')
img.save('assets/tray_icon.png')
```

Run once; commit the PNG. A nicer icon (J monogram, bracket frame, watercolor orb) is a Day 30 polish.

**File contents — signatures first:**

```python
class TrayManager:
    """System tray icon with Show / Quit menu. Runs pystray on a daemon thread
    so it doesn't block the PyWebView main loop."""

    def __init__(self, window: webview.Window, icon_path: Path,
                 on_quit: Callable[[], None]):
        ...

    def start(self) -> None:
        """Start the pystray event loop in a background thread."""
        ...

    def show_window(self) -> None:
        """Make the PyWebView window visible. Called by tray 'Show' menu."""
        ...

    def hide_window(self) -> None:
        """Hide the PyWebView window. Called by frontend close handler."""
        ...

    def quit(self) -> None:
        """Stop tray, then call the on_quit callback (which shuts down backend)."""
        ...
```

**Tray menu structure:**

```
[Cyan dot icon] JARVIS
  Show
  Quit
```

Single-click on the tray icon also calls `show_window()` — standard Windows tray behavior.

**Threading note:** `pystray.Icon.run()` blocks. Use `Icon.run_detached()` if available in your installed version; otherwise start it via `threading.Thread(target=icon.run, daemon=True).start()`. Daemon flag is critical — without it, the tray thread keeps the process alive after main exit.

### T-3. Window-hide endpoint + frontend wiring

**Backend file:** `backend/api/window.py` (new — small)

Two new endpoints replacing the current "X closes app" flow:

```python
@router.post("/window/hide")
async def hide_window():
    """Hide the PyWebView window; backend keeps running. Called by frontend X button."""
    tray_manager.hide_window()
    return {"status": "hidden"}

# /shutdown already exists from Day 7; tray Quit calls it.
```

Register the router in `backend/main.py`.

**Frontend file:** `frontend/src/components/HeaderBand.tsx` (light edit)

Currently the X button (or `onClose` prop) triggers `closeApp()` in `App.tsx` which POSTs to `/shutdown`. Change in `App.tsx`:

```ts
async function closeApp() {
  // X minimizes to tray. Backend keeps running; tray icon brings it back.
  // Real quit is only available from the tray menu's Quit item.
  await fetch(`${API_BASE}/window/hide`, { method: "POST" }).catch(() => {});
}
```

That's the entire frontend change for tray UX. The X button visual stays the same.

**Edge case to handle:** what if the user closes the window before the backend is ready? Unlikely (the X button doesn't render until the React app loads, which requires the backend WebSocket to be reachable), but defensive: the fetch already has `.catch(() => {})` so a failure is silent.

### T-4. Connection-state dot — frontend

**File:** `frontend/src/hooks/useWebSocket.ts` (extend)

Add connection state alongside the existing exports:

```ts
export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

export interface VoiceEventsHook {
  events: VoiceEvent[];
  dispatch: Dispatch<QueueAction>;
  amplitudeRef: React.MutableRefObject<number>;
  connectionState: ConnectionState;  // NEW
}
```

**State machine inside the hook:**

```ts
const [connectionState, setConnectionState] = useState<ConnectionState>('connecting');
const retryCountRef = useRef(0);
const DISCONNECTED_THRESHOLD = 5;

// In connect():
ws = new WebSocket(WS_VOICE_URL);
ws.onopen = () => {
  setConnectionState('connected');
  retryCountRef.current = 0;
};
ws.onmessage = ...  // unchanged

ws.onclose = () => {
  amplitudeRef.current = 0;
  dispatch({ type: "clear" });
  retryCountRef.current += 1;
  if (retryCountRef.current >= DISCONNECTED_THRESHOLD) {
    setConnectionState('disconnected');
  } else {
    setConnectionState('reconnecting');
  }
  if (!cancelled) retryTimer = setTimeout(connect, 1000);
};
```

The retry counter resets on successful `onopen`. So an isolated blip recovers cleanly without ever showing the red "disconnected" state.

**File:** `frontend/src/components/HeaderBand.tsx` (light edit)

The `statusDotColor` prop already exists (forward-prepared in Day 17). Replace it with a `connectionState` prop and map internally:

```ts
interface HeaderBandProps {
  connectionState: ConnectionState;
  onToggleSettings: () => void;
  onClose: () => void;
}

const DOT_COLORS: Record<ConnectionState, string> = {
  connecting:   '#EF9F27',  // amber — pre-connect counts as "trying"
  connected:    '#7CD4E8',
  reconnecting: '#EF9F27',
  disconnected: '#E24B4A',
};
```

**Optional but cheap polish:** a slow pulse on the amber dot during `reconnecting` so it visibly differs from a static color. Use a CSS keyframe animation. ~5 extra minutes.

**File:** `frontend/src/App.tsx`

Pass `connectionState` from the hook down to `HeaderBand`:

```ts
const { events, dispatch, amplitudeRef, connectionState } = useVoiceEvents();
// ...
<HeaderBand
  connectionState={connectionState}
  onToggleSettings={() => setSettingsOpen(s => !s)}
  onClose={closeApp}
/>
```

---

## What's deliberately out of scope today

- **Multi-monitor support** — primary monitor only; snap targets computed against `screens[0]`. Day 19 polish if it bugs you.
- **Animated snap transitions** — instant only per locked spec.
- **More tray menu items** — Show / Quit only. No mute toggle, no project switcher, no nested menus.
- **A nicer tray icon** — placeholder cyan dot is fine for Day 18; Day 30 polish.
- **Hover-to-fade window edges, PTT visual signal, active project in header** — explicitly dropped per the "drop additional features" decision.
- **The `project_changed` WebSocket broadcast** that would auto-update the project dropdown when changed by voice. Day 21 (project memory tools) territory.
- **Window resize handles** — locked-size frameless window stays as designed.

---

## Manual test checklist (run at end of day)

1. **TTS calibration is acceptable** — record actual value in journal.
2. **StatusBar RAF idle optimization** — open Chrome DevTools, switch to Performance tab, record 5s while idle, verify no per-frame style writes for the bar elements.
3. **Snap test — all four corners** — drag the window slowly toward each corner, release within 60px, verify it snaps. Release >60px away, verify it stays where dropped.
4. **Snap boundary feel** — release at exactly the boundary (eyeball it, ~60px from corner). Should snap *or* stay; either is acceptable as long as it's deterministic per release.
5. **Position persistence** — snap to top-right, quit the app fully, relaunch. Window appears at top-right on startup.
6. **Position persistence after non-snap release** — drag to middle of screen, release (no snap), quit, relaunch. Window appears in the middle.
7. **Tray icon appears** on app launch. Cyan dot visible in the system tray notification area.
8. **Click X — window hides, tray icon stays** — verify the backend is still running (e.g., the voice loop still responds when you re-show).
9. **Tray single-click brings window back** — same position as when hidden.
10. **Tray right-click → Show** — same effect as single-click.
11. **Tray right-click → Quit** — backend shuts down, tray icon disappears, hotkey listener stops. Confirm with Task Manager that python processes are gone.
12. **Connection dot — cyan when connected** — fresh launch with backend running.
13. **Connection dot — amber when reconnecting** — kill the backend process while frontend is running (Task Manager → end task). Dot should transition to amber within 1s, stay amber while retries happen.
14. **Connection dot — red when disconnected** — leave backend killed; after 5 retry attempts (~5s), dot transitions to red.
15. **Connection dot recovers** — restart backend; dot returns to cyan once WebSocket reconnects.
16. **Full voice loop still works** — hold Alt+Space, ask a question, get a response. Everything Day 17 worked, still works.
17. **CPU baseline** — idle CPU under 10%. Record actual value in journal. The RAF optimization may bring it lower than Day 17's number.

---

## Completion criteria

- [ ] PyWebView version verified; `window.events.moved` confirmed working
- [ ] `backend/desktop/launcher.py` vs `backend/desktop.py` module conflict resolved if it existed
- [ ] `backend/desktop/snap.py` exists; `SnapManager` snaps to nearest corner within 60px
- [ ] `data/window_state.json` written on every move; read on launch; window restores correctly
- [ ] `backend/desktop/tray.py` exists; pystray runs on daemon thread
- [ ] Tray icon visible in system tray on launch
- [ ] X button calls `/window/hide`, not `/shutdown`; window hides but backend continues
- [ ] Tray menu Show / Quit both work; Quit fully terminates backend + tray + hotkey thread
- [ ] `useVoiceEvents` exports `connectionState`; reconnect logic counts retries and transitions correctly
- [ ] HeaderBand dot color reflects connectionState; amber pulse during reconnecting (optional)
- [ ] StatusBar RAF skips writes when idle; verified via DevTools Performance
- [ ] TTS calibration value confirmed acceptable or tuned; logged in journal
- [ ] All 17 manual tests pass
- [ ] CPU under 10% in idle (record actual value in journal)

---

## Watch-outs / gotchas

- **pystray + PyWebView threading is fragile.** Both libraries assume control of the "main" GUI thread on Windows. PyWebView must own the main thread (its `webview.start()` blocks). pystray runs in a daemon thread. If you reverse this, the window won't render. Daemon flag on the tray thread is non-negotiable — without it, Quit from tray leaves a zombie Python process.
- **PyWebView 4.x `events.moved` fires on drag-end, not during drag.** Good (we don't want to fire `move()` 100 times during a drag — would feel like a fight against the user). But verify on your installed version; older versions emit per-frame.
- **`window.move(x, y)` while the user is mid-drag** is undefined behavior on Windows transparent windows. Don't call it from anywhere except the `on_moved` callback (which fires after drag-end) or `restore_position` (which fires once at startup).
- **`data/window_state.json` can become corrupted** if the app crashes mid-write. Use the standard write-temp-then-rename pattern, or `pathlib.Path.write_text(..., newline=...)`. Read errors should fail to a sensible default (center of primary screen) and log a warning — never crash on a missing/corrupt state file.
- **Hidden PyWebView window still receives WebSocket events.** When hidden via `window.hide()`, the React app keeps running; the WebSocket stays connected; the RAF loops keep ticking. This is the desired behavior (voice loop must keep working) but it means the assistant is consuming the same CPU as when visible. If this becomes a concern, a future polish day could pause RAF loops while hidden.
- **First click on tray icon after a long idle period** can be slow on Windows (~500ms). pystray polls; the OS has its own event lag. Acceptable.
- **The `connectionState === 'connecting'` state at app startup is brief** (typically <200ms). The amber dot may flash briefly on first launch. Acceptable; if it bugs you, gate the connecting state to render only after 500ms with a setTimeout — but this is over-tuning for a transient.
- **Multiple PyWebView windows are not supported by this design.** Single-window only. If you ever add a settings popup as a separate window, the snap/tray/connection-dot wiring will need rethinking.
- **The 5-retry "disconnected" threshold is per session**, not per WebSocket instance. If the backend was down on launch, you get 5 quick failures (~5s) → red. Once it recovers, cyan. If it goes down again later, fresh counter from 0 → 5 more retries → red. This is correct behavior; just be aware that the disconnected → connected → disconnected pattern doesn't have memory.
- **Don't change the WebSocket retry delay from 1s** without recomputing the disconnected threshold. The current 5 × 1s = 5s feels right. Switching to 2s retries means 10s before red — too slow.
- **Tray icon doesn't appear on Wine / VM environments** sometimes due to missing tray support. If you test the demo recording on a clean VM, verify tray works. Otherwise plan to demo on the dev machine.

---

## Git commit messages

Three logical commits.

```
feat(desktop): snap-to-corner with position persistence

- SnapManager: window-center distance to 4 corner targets; 60px threshold;
  instant snap (no animation); 8px margin from screen edge
- Listens on window.events.moved (PyWebView 4.x); falls back to polling
  if event API unavailable
- Persists last position to data/window_state.json on every move-end;
  restores on window.events.shown
- Single-monitor only (uses webview.screens[0])
```

```
feat(desktop): minimize-to-tray via pystray

- TrayManager: cyan dot icon (64x64 PNG, see scripts/generate_tray_icon.py)
- Tray menu: Show / Quit (single-click also shows)
- pystray runs on daemon thread; PyWebView keeps the main thread
- Frontend X button now POSTs /window/hide (new endpoint); backend stays
  alive in tray. Tray → Quit calls existing /shutdown
- Hotkey listener + voice loop continue working while window is hidden
```

```
feat(ui): connection-state dot in header band

- useVoiceEvents exports connectionState: 'connecting' | 'connected' |
  'reconnecting' | 'disconnected'
- Retry counter; 5 consecutive failures escalate amber → red
- HeaderBand dot color mapped from state; optional pulse animation on
  amber (reconnecting)
- App.tsx threads connectionState through to HeaderBand

Also: StatusBar RAF skips per-frame writes when amp=0 AND state not
listening/speaking (Day 17 carryover, verified via DevTools).
TTS calibration max settled at <value> after Day 16 carryover check.
```

---

## Time budget

| Phase | Estimate |
|---|---|
| PF-1 TTS calibration check | 15 min |
| PF-2 StatusBar RAF guard | 15 min |
| T-1 Snap-to-corner (incl. PyWebView preflight + module conflict check) | 2 hours |
| T-2 Minimize-to-tray (incl. icon generation, threading setup) | 2 hours |
| T-3 Window-hide endpoint + frontend wiring | 30 min |
| T-4 Connection-state dot (hook + HeaderBand + App wiring) | 45 min |
| Manual testing (17 items) | 30 min |
| Buffer / unforeseen | 45 min |
| **Total** | **~6.5 hours** |

The original Day 18 estimate from "Day 17 next steps" was 4–5 hours. This is heavier because:
- The PyWebView module-conflict check could eat 30 min if it surfaces
- pystray + PyWebView threading is fragile and the watch-outs above are all things I've seen go wrong
- The pre-flight items are small but real

If the day runs long, **the safest item to defer** is T-4 (connection-state dot). The dot is forward-prepared from Day 17 so it can land on Day 19 without architectural cost. Snap and tray are harder to split.

If significantly under budget: nothing else gets added. Day 19 starts early.

---

## Workflow reminders

- **Verify PyWebView's events API before writing snap code.** If the version is wrong, you'll waste hours on a polling fallback you didn't need (or vice versa).
- **Test tray + window-hide together** as one unit. They're functionally inseparable — testing hide without tray means clicking X loses the app forever.
- **Write the state-machine table for `connectionState`** on paper before coding. Three transitions (open, close-with-retries-remaining, close-after-threshold). Easy to draw, easy to get wrong if you skip it.
- **Commit per task.** Three logical commits, three logical bodies of test. Don't batch.
- **Don't tune the snap threshold past 60px during testing** unless you find a clear feel problem. We've already prototyped this; trust the number.

---

## When you get stuck

The likely stuck-points and what to do:

- **PyWebView's `events.moved` doesn't fire / fires constantly:** version mismatch. Check the docs at `https://pywebview.flowrl.com` for your installed major version. If the event API is too different, fall back to polling `window.x/y` every 100ms via `asyncio.create_task` — uglier but works.
- **Tray icon doesn't appear:** on Windows, this is almost always a pystray import or icon path issue. Run `python -c "import pystray; print(pystray.__version__)"` to confirm install. If pystray imports but no icon shows, check that the icon path resolves and the PNG is a valid 64×64 RGBA.
- **App freezes after clicking X:** the hide call is blocking the main thread, or the tray thread isn't running. Check that the tray thread was started with `daemon=True` AND that `webview.start()` is still owning the main thread.
- **Tray Quit doesn't actually exit:** zombie threads. Verify all background threads are daemon. Verify the `/shutdown` endpoint correctly stops PyWebView (it should call `window.destroy()` then exit). Check Task Manager after Quit — no `python.exe` or `msedgewebview2.exe` should remain.
- **Connection dot stays cyan even after backend is killed:** the `onclose` handler isn't firing. Confirm in DevTools that the WebSocket is actually closing (Network tab → WS → state). If not, the backend may be holding the connection open through some other path.
- **Position persistence works on quit-from-tray but loses position on system reboot:** unlikely but possible if the JSON write hits a permissions issue. Check `data/` directory permissions; on Windows, the AppData equivalent path may be more reliable than a repo-relative `data/` for personal use.
- **Window jumps weirdly on launch:** `restore_position` is firing before the window is fully rendered. Bind to `events.shown` not `events.loaded` — `loaded` fires when the HTML is parsed but the window may not yet be at its final size.

If you're stuck >30 min on any one thing, paste the symptom + relevant code snippet and ask. Don't grind.
