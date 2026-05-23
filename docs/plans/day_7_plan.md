# Day 7 Plan — PyWebView Shell + Global Hotkey

**Period:** Day 7 of Week 1 (Foundation + PyWebView Shell)
**Builds on:** Day 2 (initial PyWebView window), Day 3 (FastAPI `/ws/voice` stub), Day 6 (semantic memory — complete)
**Enables:** Day 8 (audio capture via PTT — the hotkey events shipped today are what trigger recording tomorrow)
**Time budget:** 5–6 hours
**Target commit:** `feat: pywebview shell - transparent always-on-top with global hotkeys`

---

## 1. Today's goal (one sentence)

A transparent, always-on-top, frameless desktop window with two working global hotkeys — **Alt+Space** (push-to-talk, fires `ptt_start` on press and `ptt_end` on release) and **Ctrl+Alt+J** (mute toggle) — both flowing as WebSocket events from the Python backend into the React frontend, with visual feedback rendered for each event.

No audio capture today. No blob today. Just the **shell** and the **wires**.

---

## 2. Why Day 7 matters

Days 1–6 were all backend: settings, LLM router, SQLite, ChromaDB, importance scoring. That work is invisible — you can't show a friend a memory table.

Day 7 is the day the project first looks like *itself*: a floating overlay that lives on top of every window and responds to keys pressed anywhere on the system. It's also the first day three subsystems (pynput in its own OS thread, FastAPI in an asyncio loop, React in a webview) have to cooperate. Get the wiring right today and Day 8 (audio capture) is a 30-minute extension.

Get the wiring wrong and Day 8 is a debugging swamp. **The architecture you set up today is reused for every voice event for the rest of the project.**

---

## 3. Pre-flight (30 minutes, do not skip)

### 3.1 Finish Day 6's open item — manual C7 verification

The Day 6 status doc lists this as still pending:

```powershell
# 1. In .env, temporarily set GEMINI_API_KEY to an invalid string
# 2. Start the server
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 3. Send a chat request
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" `
  -d "{\"message\": \"test message\"}"

# Expected: 200 response (possibly with a graceful error message in the body)
# NOT expected: 500 status, unhandled exception in the log
# 4. Check data/logs/jarvis.log for a WARNING-level "memory" log line, not an ERROR-with-stacktrace
# 5. Restore the real API key in .env. Restart server. Confirm normal /chat works.
```

If this fails, **fix it before starting Day 7.** A shell built on a backend that 500s on first error will hide its own bugs behind backend bugs.

### 3.2 Audit existing PyWebView code

`backend/desktop.py` already exists from Day 2. Read it before writing anything new today.

```powershell
# How many lines?
Get-Content backend/desktop.py | Measure-Object -Line

# What's in it?
code backend/desktop.py
```

Three possibilities, each with a different plan-for-the-day:

| State of `backend/desktop.py` | Action today |
|---|---|
| Has `webview.create_window(...)` with `frameless=True, on_top=True, transparent=True` | Skip to §5 (frontend drag region + close button) |
| Has a window but missing flags (e.g. has a title bar, or not on top) | §4 first — add the missing config |
| File only has placeholder/hello-world content | §4 in full — write the window config from scratch |

### 3.3 Confirm the WebSocket endpoint is reachable

Day 3 added `WS /ws/voice` as a stub. Confirm it still accepts connections before we extend it:

```powershell
# From PowerShell, a tiny smoke test using the .NET ClientWebSocket OR just check from React:
# Open browser devtools at http://localhost:5173, in console:
# > const ws = new WebSocket('ws://localhost:8000/ws/voice'); ws.onopen = () => console.log('ok');
```

If the WebSocket doesn't open: that's a Day 3 regression, fix it before extending it.

### 3.4 Git hygiene

```powershell
git status        # should be clean (Day 6 already committed)
git pull          # in case anything moved
git log --oneline -5
```

---

## 4. Decision points — surface these BEFORE writing code

Two real decisions today. Each is hard to reverse after code lands. Discuss with Claude Code before committing to one.

### Decision A — Alt+Space conflict with Windows system menu

By default, Alt+Space on Windows opens the active window's **system menu** (Move / Size / Close). If we register Alt+Space without suppressing it, every PTT press flashes the system menu briefly. Annoying but harmless.

Two choices:

| Option | Pro | Con |
|---|---|---|
| **A1 — Suppress globally** (pynput `suppress=True`) | Clean: no flash, no menu | Kills Alt+Space *system-wide* while Jarvis is running. Power users who use Alt+Space for the system menu lose it |
| **A2 — Accept the flash** (no suppression) | Doesn't break a Windows convention | Visual flash every PTT press; might lag the hotkey firing slightly while Windows draws the menu |
| **A3 — Different hotkey** (e.g. Ctrl+Shift+Space or Ctrl+`) | No conflict, no suppression needed | Two-handed for a one-handed feature; deviates from V1 plan |

**Recommendation: A1 (suppress globally).** This is a daily-driver tool — you want PTT to feel instant. Sacrificing Alt+Space's system-menu function for the duration the app is running is fine for a single-user tool. If it later annoys you, swap to Ctrl+Shift+Space.

**Ask before writing.** This is the kind of choice CLAUDE.md says to surface.

### Decision B — Bridging pynput's thread to asyncio

`pynput.keyboard.Listener` runs in its own native thread. WebSocket broadcasts (FastAPI) live in the asyncio event loop. The two cannot talk directly — calling an async function from a sync thread blows up.

Two patterns to bridge:

| Pattern | How it works | Trade-off |
|---|---|---|
| **B1 — `asyncio.run_coroutine_threadsafe(coro, loop)`** | Hotkey thread submits a coroutine to the main loop; main loop runs it | Need a reference to the running loop. Each hotkey schedules a real coroutine — slightly more overhead |
| **B2 — `loop.call_soon_threadsafe(queue.put_nowait, event)` + asyncio task draining queue** | Hotkey thread pushes a plain event into an `asyncio.Queue`; a long-running task in the main loop drains the queue and does the broadcast | Cleaner separation. Easy to add backpressure, replay, or filtering later |

**Recommendation: B2.** The queue is essentially the same `state machine event bus` pattern that Day 11 (`services/conversation.py`) needs anyway. Building it today means Day 11 inherits it for free.

**Ask before writing.** Trivially reversible only until something else starts pushing into the queue.

---

## 5. Architecture for Day 7 (sketch on paper before coding)

```text
   ┌─────────────────────────────────────────────────────────────┐
   │  pynput.keyboard.Listener  (native OS thread)               │
   │  - tracks modifier state (alt, ctrl)                        │
   │  - on press of Space while Alt held → emit "ptt_start"      │
   │  - on release of Space (or Alt) → emit "ptt_end"            │
   │  - on press of J while Ctrl+Alt held → emit "mute_toggle"   │
   └────────────────┬────────────────────────────────────────────┘
                    │ loop.call_soon_threadsafe(queue.put_nowait, event)
                    ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  asyncio.Queue   (in FastAPI's event loop)                  │
   │  - one canonical event stream                               │
   │  - decoupled from how events were produced                  │
   └────────────────┬────────────────────────────────────────────┘
                    │
                    ▼  (long-running task: events_broadcaster)
   ┌─────────────────────────────────────────────────────────────┐
   │  WebSocketConnectionManager.broadcast(event)                │
   │  - iterates active WS connections                           │
   │  - sends JSON {"type": "ptt_start" | "ptt_end" |            │
   │                  "mute_toggle", "ts": ...}                  │
   └────────────────┬────────────────────────────────────────────┘
                    │ WebSocket /ws/voice
                    ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  React (PyWebView)                                          │
   │  - useWebSocket hook                                        │
   │  - on "ptt_start": status badge → "🎙 listening"            │
   │  - on "ptt_end": status badge → "idle"                      │
   │  - on "mute_toggle": badge toggles between muted/idle       │
   │  - console.log every event for verification                 │
   └─────────────────────────────────────────────────────────────┘
```

**Sketch this on paper before opening the editor.** If you can't draw it without looking at the markdown, you don't understand it yet — ask Claude Code to walk through it with you.

---

## 6. Tasks (sequenced, with the *why*)

### Task 1 — `backend/desktop.py` window config (45 min)

**Why this first:** the window must already be running before you can verify hotkey events render visually in it.

What goes in:
- `webview.create_window(...)` with `frameless=True, on_top=True, transparent=True`
- Size: 400 × 600 (per V1 plan)
- Initial position: bottom-right corner of primary monitor (compute `x = screen_width - 420`, `y = screen_height - 700` with a small margin)
- URL: `http://localhost:5173` in dev (Vite); a check for `settings.frontend_url` so prod can swap to file-served `dist/index.html` later
- `webview.start()` — call it at module-bottom inside `if __name__ == "__main__"` guard

**Things to ask Claude Code:**
- "How do I get primary monitor dimensions without a heavyweight dep?" (likely `tkinter.Tk().winfo_screenwidth()` then `.destroy()` — or PyWebView has a helper)
- "Does `transparent=True` need anything in `webview.start()`?" (no, but it's worth checking)

**Don't yet:** wire pynput. The window must work in isolation first.

**Verify:**
```powershell
# Terminal 1
cd frontend; npm run dev

# Terminal 2
python -m backend.desktop
```
Window appears in bottom-right, transparent (you should see your desktop through anywhere the React app isn't drawing), and floats above other windows. If React still has its default `bg-white` background, change it to `bg-transparent` first (next task).

**Watch for:**
- Black flicker on first paint — normal on Windows, accepts as-is (noted in `SKILL.md`)
- Vite dev server must be running first; PyWebView won't auto-start it. Update README later (Day 29) to mention this

---

### Task 2 — Frontend root: transparent + drag region + close button (45 min)

**Why this second:** the window config in Task 1 means nothing if React paints a solid background over it. Also need the drag region before testing move-by-drag.

Edits to `frontend/src/`:

1. **Transparent root** — in `index.css` or `App.css`:
   ```css
   html, body, #root {
     background: transparent !important;
     margin: 0;
     padding: 0;
   }
   ```

2. **Header drag bar** — a thin (~32px) bar at the top of `App.tsx`:
   ```tsx
   <div
     style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
     className="h-8 w-full ..."
   >
     {/* Drag area — empty or contains app title */}
     <button
       style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
       onClick={() => /* close handler */}
     >
       ✕
     </button>
   </div>
   ```
   `-webkit-app-region: drag` is a Chromium feature — PyWebView's underlying webview engine on Windows (Edge WebView2) honors it.

3. **Close button handler** — calls a backend endpoint that triggers `webview.windows[0].destroy()`. Simplest: add `POST /shutdown` to FastAPI that gracefully kills the process. Wire frontend button to call it via `fetch`. (Don't overthink — single-user app, no auth needed on this endpoint.)

4. **Visible test shape** so you know the window's there even with no content:
   ```tsx
   <div className="m-4 h-24 w-24 rounded-full bg-cyan-400/50" />
   ```
   You should see a translucent cyan circle floating on your desktop. Delete this on Day 15 when the real blob lands.

**Watch for:**
- TypeScript will complain about `WebkitAppRegion` not being in `CSSProperties` — cast with `as React.CSSProperties` and add a comment that this is a Chromium-specific property. Don't disable the type system globally
- If drag doesn't work: confirm the bar has actual area (height > 0, width > 0). Easy to miss with `h-0`

**Verify:**
- Drag the header bar → window moves
- Click the close button → app exits cleanly (no zombie Python process; check Task Manager)
- The cyan test shape is visible against your wallpaper

---

### Task 3 — `backend/desktop/hotkeys.py` (90 min — biggest task today)

**Why this third:** now that the window is real and visible, we can verify hotkey events drive frontend state.

Structure of the file:

```python
# backend/desktop/hotkeys.py

# This module runs pynput's keyboard Listener on a background OS thread.
# Events are pushed into an asyncio.Queue (set up by main.py) via
# loop.call_soon_threadsafe, which is the only thread-safe way to put items
# into an asyncio.Queue from a non-async-loop thread.

from pynput import keyboard
import asyncio
from loguru import logger

# Modifier-state tracking. pynput's Listener fires per-key, so we track
# whether Alt and Ctrl are currently held to detect combos like Alt+Space.
_state = {
    "alt": False,
    "ctrl": False,
    "ptt_active": False,  # True between ptt_start and ptt_end
}

# These get injected by main.py at startup so this module doesn't have to
# import from main (avoids circular imports).
_loop: asyncio.AbstractEventLoop | None = None
_event_queue: asyncio.Queue | None = None


def init(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
    """Wire in the asyncio loop and event queue from main.py."""
    global _loop, _event_queue
    _loop = loop
    _event_queue = queue


def _emit(event_type: str) -> None:
    """Thread-safe push of an event onto the asyncio queue."""
    if _loop is None or _event_queue is None:
        logger.warning(f"hotkey event {event_type} dropped: loop/queue not initialised")
        return
    _loop.call_soon_threadsafe(_event_queue.put_nowait, {"type": event_type})


def _on_press(key) -> None:
    # ... detect Alt held + Space pressed → ptt_start (only fire once per held)
    # ... detect Ctrl+Alt+J → mute_toggle
    ...


def _on_release(key) -> None:
    # ... detect Space released OR Alt released → ptt_end (only if ptt_active)
    ...


def start_listener() -> keyboard.Listener:
    """Start the pynput Listener in its own thread. Returns it for shutdown."""
    listener = keyboard.Listener(
        on_press=_on_press,
        on_release=_on_release,
        suppress=False,  # Set True after Decision A is finalized
    )
    listener.start()
    logger.info("global hotkey listener started (Alt+Space, Ctrl+Alt+J)")
    return listener
```

**Things to ask Claude Code as you write:**
- "How do I detect that `key` is the Space key? `key == keyboard.Key.space`?" (yes)
- "How do I detect a character key like 'J' regardless of caps-lock? `hasattr(key, 'char') and key.char.lower() == 'j'`?" (close — also handle `key.vk` for some kbd layouts)
- "If `suppress=True`, does that suppress *every* key, or only my hotkey combo?" (every key — not what we want; pynput suppression is all-or-nothing on Listener level; for per-combo suppression use `keyboard.GlobalHotKeys` instead, but that's press-only and doesn't handle PTT release. **This is why we're using Listener + manual tracking.**)

**Decision A revisited:** `suppress=False` on the Listener means Windows still sees Alt+Space and pops the system menu. The "right" answer for per-combo suppression with both press and release is to use the lower-level Win32 keyboard hook (the `keyboard` library does this, pynput doesn't expose it cleanly). For Day 7, accept the system-menu flash and move on. **Day 13 buffer day** is the right place to revisit if it annoys you.

If you want a clean Alt+Space without the flash today, swap to Ctrl+Shift+Space (no conflict, no suppression needed). One-line change. Tell Claude Code your decision.

**Watch for:**
- pynput on Windows sometimes needs `pynput.keyboard.Listener` started before the main thread does anything blocking. PyWebView's `webview.start()` is blocking. The order matters: start the hotkey listener, then call `webview.start()` last. See Task 5.
- pynput exceptions inside callbacks get swallowed silently. Wrap `_on_press` and `_on_release` bodies in `try/except` that logs.
- Cleanup: when the window closes, the listener thread won't stop on its own. Save the `Listener` reference and `.stop()` it on shutdown.

**Verify (interim — events appear in logs only, frontend wiring comes next):**
- Run `python -m backend.main`
- Hold Alt, press Space → log shows `_emit('ptt_start')`
- Release Space → log shows `_emit('ptt_end')`
- Hold Ctrl+Alt, press J → log shows `_emit('mute_toggle')`
- Press Space without Alt → no event
- Press Alt alone → no event

---

### Task 4 — WebSocket connection manager + queue drainer (45 min)

**Why this fourth:** events are flowing into the queue, but no one is reading them out.

Two pieces:

**(a) `backend/api/voice.py`** — extend the existing `/ws/voice` endpoint with a `ConnectionManager`:

```python
# backend/api/voice.py

# Standard FastAPI WebSocket connection manager pattern. Holds active
# connections in a set; broadcast() sends to all. Used for fan-out of
# voice/hotkey state events from a single producer to multiple potential
# clients (in v1 there's only one — the React app — but the pattern
# generalises trivially).

class ConnectionManager:
    def __init__(self):
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.add(ws)

    def disconnect(self, ws: WebSocket):
        self._connections.discard(ws)

    async def broadcast(self, message: dict):
        # Iterate over a copy in case a send fails and we need to remove
        # the dead connection mid-iteration.
        for ws in list(self._connections):
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"failed to send to ws, dropping: {e}")
                self.disconnect(ws)


manager = ConnectionManager()


@router.websocket("/ws/voice")
async def voice_ws(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Day 7: client doesn't send anything yet, we just hold the connection
        # so we can push state to it. Future days will accept client messages
        # (e.g. settings updates).
        while True:
            await ws.receive_text()  # Will block; we don't process the message yet
    except WebSocketDisconnect:
        manager.disconnect(ws)
```

**(b) Queue drainer in `backend/main.py` startup:**

```python
# In main.py's lifespan/startup handler:

from backend.api.voice import manager as ws_manager
from backend.desktop import hotkeys

async def _drain_events(queue: asyncio.Queue):
    """Long-running task that reads hotkey events and broadcasts them."""
    while True:
        event = await queue.get()
        await ws_manager.broadcast(event)

# On startup:
event_queue: asyncio.Queue = asyncio.Queue()
loop = asyncio.get_running_loop()
hotkeys.init(loop=loop, queue=event_queue)
hotkeys.start_listener()
asyncio.create_task(_drain_events(event_queue))
```

**Watch for:**
- The order matters: `init()` must run before `start_listener()`, or early events drop with the "loop/queue not initialised" warning
- `asyncio.create_task` returns a task that *can be garbage-collected if you don't hold a reference*. Store it on the app state: `app.state.drainer_task = asyncio.create_task(...)`. This bites people regularly.
- If the queue ever fills (unbounded by default — fine for hotkeys at <10/sec), that's a backpressure issue. Cap at `maxsize=100` for safety; PTT-spamming shouldn't bring down the system.

**Verify:**
- Start backend with `python -m backend.main` (or however you run it; check existing pattern from Day 3)
- Open browser devtools → Network → WS → connect to `ws://localhost:8000/ws/voice`
- Press Alt+Space → see `{"type": "ptt_start"}` in the WS frames panel
- Release → see `{"type": "ptt_end"}`

---

### Task 5 — Boot order: hotkeys must start before `webview.start()` (15 min)

**Why this matters:** `webview.start()` blocks the main thread. Anything you want running in parallel — the FastAPI server, the hotkey listener — must be started in background threads/processes *before* the `webview.start()` call.

A common pattern (verify Day 2's `desktop.py` actually does this):

```python
# backend/desktop.py — top-level orchestrator

import threading
import uvicorn
import webview

def _run_backend():
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, log_level="info")

if __name__ == "__main__":
    # Start FastAPI (which itself starts the hotkey listener via lifespan)
    # on a daemon thread so it dies when the window closes.
    backend_thread = threading.Thread(target=_run_backend, daemon=True)
    backend_thread.start()

    # Give uvicorn ~1s to bind the port. There are cleaner approaches
    # (poll /health) but this is fine for a dev tool.
    import time; time.sleep(1.0)

    window = webview.create_window(
        "Jarvis",
        url="http://localhost:5173",
        width=400, height=600,
        x=screen_width - 420, y=screen_height - 700,
        frameless=True, on_top=True, transparent=True,
    )
    webview.start()  # Blocks here until window closes
```

**Things to verify with Claude Code:**
- Does Day 2's `desktop.py` already start the backend? If yes, integrate; if no, this section is where it lands.
- Is `uvicorn.run` happy being called from a non-main thread? (Yes, with caveats around signal handlers — fine for our use.)

**Watch for:**
- Reload loops: `uvicorn.run(..., reload=True)` doesn't play nice with non-main-thread launch. Keep `reload=False` for the integrated launch. Develop with separate `uvicorn` + `pywebview` terminals; integrate only after each works in isolation.
- Daemon threads die when the main thread (PyWebView) exits — that's what you want.

---

### Task 6 — React: WebSocket hook + visual feedback (30 min)

**Why this last:** with all backend wires in place, the frontend change is small and verifying it closes the loop.

Build or extend `frontend/src/hooks/useWebSocket.ts`:

```tsx
// frontend/src/hooks/useWebSocket.ts
// Minimal WS hook for Day 7. Future days will extend this with reconnect,
// message-type routing, and state-machine subscription helpers.

import { useEffect, useState } from "react";

export type VoiceEvent =
  | { type: "ptt_start" }
  | { type: "ptt_end" }
  | { type: "mute_toggle" };

export function useVoiceEvents() {
  const [last, setLast] = useState<VoiceEvent | null>(null);

  useEffect(() => {
    const ws = new WebSocket("ws://localhost:8000/ws/voice");
    ws.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as VoiceEvent;
        console.log("voice event:", ev);  // <-- verification per plan
        setLast(ev);
      } catch (err) {
        console.error("bad WS payload:", e.data, err);
      }
    };
    ws.onerror = (e) => console.error("ws error:", e);
    return () => ws.close();
  }, []);

  return last;
}
```

In `App.tsx`, render a tiny status badge that reflects state:

```tsx
const last = useVoiceEvents();
const [muted, setMuted] = useState(false);

useEffect(() => {
  if (last?.type === "mute_toggle") setMuted((m) => !m);
}, [last]);

const label = muted ? "muted"
  : last?.type === "ptt_start" ? "listening"
  : "idle";

return (
  <div>
    <div className="h-8 ...drag bar...">...</div>
    <div className="m-4 p-2 rounded bg-white/20 text-white">
      Status: {label}
    </div>
  </div>
);
```

**Watch for:**
- React strict mode in dev mounts effects twice → opens two WebSocket connections. The cleanup `ws.close()` handles it. If you see double events, that's why.
- Hot-reload during dev will sometimes leave zombie WebSocket clients. If frames look wrong, restart Vite.

---

### Task 7 — End-to-end manual test + commit (30 min)

Run the integrated app (`python -m backend.desktop`), then with the window in the corner:

1. **Focus a totally different app** (browser, Word, etc.). Confirm Jarvis window is still visible on top.
2. Hold Alt+Space → status badge changes to "listening". Console logs `{type: "ptt_start"}`.
3. Release → status returns to "idle". Console logs `{type: "ptt_end"}`.
4. Press Ctrl+Alt+J → status changes to "muted".
5. Press Ctrl+Alt+J again → returns to "idle".
6. While "muted", hold Alt+Space → events still fire to the WS (frontend will ignore them — that logic lives in Day 11, not today). Just confirm the events are produced; mute *behavior* comes later.
7. Drag the window header → window moves. Click close button → app exits cleanly. Open Task Manager, confirm no leftover `python.exe` or `webview2` process.

Once all green:

```powershell
git add -A
git diff --cached --stat       # sanity check the diff isn't sprawling
git commit -m "feat: pywebview shell - transparent always-on-top with global hotkeys"
```

Add one line to `docs/journal.md`:

```
Day 7: PyWebView shell + Alt+Space PTT + Ctrl+Alt+J mute, all wired through asyncio.Queue → WebSocket → React. Decisions: B2 queue pattern, A2 accept-system-menu-flash for now.
```

---

## 7. Completion criteria (from v2 plan, reproduced for the checklist)

- [ ] Window is transparent (desktop visible behind it)
- [ ] Stays on top of all other windows
- [ ] Drag-to-move works
- [ ] Pressing Alt+Space anywhere in Windows fires an event in React (visible in dev tools)
- [ ] Releasing Alt+Space fires the release event
- [ ] Ctrl+Alt+J fires mute event
- [ ] You understand how PyWebView + pynput + WebSocket talk to each other (sketch the flow on paper)

Plus today's extras:

- [ ] No zombie processes after window close
- [ ] Backend logs every hotkey event at INFO level (helps debugging tomorrow)
- [ ] Day 6 C7 verification (the open item) confirmed
- [ ] Architecture sketched on paper, photographed, dropped in `docs/`

---

## 8. Watch-out list (gotchas surfaced from `SKILL.md` and Day 6 status)

| Gotcha | Where it bites | Mitigation |
|---|---|---|
| PyWebView transparency needs both `transparent=True` AND CSS `background: transparent` | Task 1 + Task 2 — must do both | Test order: do CSS first, then PyWebView config; otherwise white React layer hides everything |
| Black flicker on first paint (Windows) | Task 1 — first launch | Accept, document. Not a bug |
| pynput callback exceptions get swallowed | Task 3 — silent broken hotkey | Wrap callbacks in try/except + log |
| Alt+Space flashes Windows system menu | Task 3 — every PTT press | Accept (A2) or swap to Ctrl+Shift+Space |
| `asyncio.create_task` result GC'd silently | Task 4 — drainer task vanishes after a few hotkeys | Store on `app.state.drainer_task` |
| `uvicorn.run(reload=True)` + non-main-thread = misery | Task 5 — startup hangs or signals misbehave | `reload=False` when launched from `desktop.py`; use reload only for `uvicorn backend.main:app` standalone |
| React strict mode doubles `useEffect` | Task 6 — see two WS connections | Cleanup function handles it; ignore the warning |
| WebView2 lingering after window close | Task 7 — zombie process | If it happens, ensure `webview.start()` is the last call and that the FastAPI thread is `daemon=True` |
| Stale uvicorn process holding port 8000 (from Day 6 P3) | Anywhere code restarts | `netstat -ano \| findstr :8000` to find, `Stop-Process -Id <pid>` to kill |

---

## 9. If today goes sideways

Per CLAUDE.md ("when I'm stuck"), if you hit a wall:

1. **Don't immediately rewrite.** Sketch what you expect to happen vs. what is happening.
2. **Reduce.** If the integrated app misbehaves, run the pieces in isolation: `python -m backend.main` alone, then `npm run dev` alone, then `python -m backend.desktop` alone with hotkeys disabled, then add hotkeys.
3. **Don't add fallbacks.** This is a fresh shell; if pynput doesn't fire on Windows, the answer is to fix pynput, not to add a "if pynput not working, do X" branch.
4. **Time-box.** If a single sub-task (e.g. transparency on Windows) eats > 90 minutes, descope: accept a solid-color window today, file a ticket in `docs/journal.md`, come back on Day 13/14 buffer.

The drop order if Day 7 overruns into Day 8:
1. Window transparency — keep it; visually defines the project
2. Drag + close button — keep it; trivial
3. Alt+Space PTT — **keep it**; Day 8 depends on it
4. Ctrl+Alt+J mute — defer to Day 11 (mute is a Day 11 deliverable anyway, just bring it forward to today was a bonus)
5. WS event broadcast — keep it; Day 8 depends on it
6. Status badge in React — defer; Day 15 builds real visual feedback

---

## 10. What this unlocks for tomorrow

**Day 8 — Audio Capture via PTT** becomes: subscribe to `ptt_start` in the conversation service, call `audio.start_recording()`; subscribe to `ptt_end`, call `audio.stop_recording()`, write WAV file. That's the entire day if today's wiring is clean.

If today's wiring is fragile (hotkey events dropping, race conditions on rapid press/release, queue backpressure), Day 8 doubles in scope because every audio bug has to be triaged as "audio problem OR hotkey-wiring problem".

**The single most valuable artifact Day 7 produces is not the window — it's a clean, reusable event bus from a sync background thread to the asyncio main loop.** Days 8, 11, 16, 20 all reuse this pattern.

---

## 11. Skills/docs updates (housekeeping)

After Day 7 commits cleanly, update **`.claude/skills/project-architecture/SKILL.md`** (if a new fact emerged):

- Add to "Project-specific gotchas" anything you learned about PyWebView transparency or pynput threading on *your specific* Windows version that contradicts or refines the existing notes.
- If you chose the asyncio-queue pattern (B2), add a one-paragraph note under "Patterns to follow" so Days 11/16 inherit it without rediscovery.

Do not write a new skill file today. The natural skill candidate ("hotkey + WS event pattern") is too specific to crystallise yet — see if it's still the right shape after Day 11 uses it, then write it then.
