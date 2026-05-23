# Project Status — Day 7

**Period covered:** Day 7 (PyWebView Shell + Global Hotkeys)
**Status:** Complete — all completion criteria met. Committed as `feat: pywebview shell - transparent always-on-top with global hotkeys`.
**Environment:** Windows 11, Python 3.13.5, pywebview 5.x, pynput 1.x, Edge WebView2

> Checkpoint summary for Day 7: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 8.

---

## 1. What has been done

Day 7 wired together three subsystems that had never spoken to each other before: pynput
running in a native OS thread, FastAPI/uvicorn running in its own asyncio event loop on a
background daemon thread, and a React app rendered inside an Edge WebView2 window managed
by PyWebView. The result: a single command starts everything, and pressing Alt+Space
anywhere on the system changes the status badge in the floating overlay within milliseconds.

| Task | What landed | Status |
|---|---|---|
| 1 — Window positioning | tkinter screen-size query; `x = screen_w - 420, y = screen_h - 700`; URL from `settings.frontend_dev_url` | Done |
| 2 — Frontend shell | Drag bar (`-webkit-app-region: drag`), close button (calls `POST /shutdown`), cyan test circle, status badge placeholder | Done |
| 3 — `hotkeys.py` | pynput `Listener` on OS thread; `_state` dict for modifier tracking; `_emit()` via `loop.call_soon_threadsafe`; closed-loop guard | Done |
| 4 — WS ConnectionManager + drainer | `ConnectionManager` with fan-out `broadcast()`; `_drain_events` task wired in `main.py` lifespan; task stored on `app.state` | Done |
| 5 — Boot order | `_run_backend()` on daemon thread before `webview.start()`; `time.sleep(1.0)` startup delay | Done |
| 6 — React hook + status badge | `useVoiceEvents()` hook with 1-second reconnect loop; `statusLabel` derived from `muted` + `lastEvent`; badge updates in real-time | Done |

**Completion criteria verified:**

| Criterion | Result |
|---|---|
| Window is transparent, always-on-top | ✅ Confirmed visually |
| Stays on top of all other windows | ✅ Confirmed visually |
| Drag-to-move works | ✅ `-webkit-app-region: drag` working in Edge WebView2 |
| Alt+Space fires `ptt_start` in React (visible in DevTools) | ✅ Confirmed in logs and status badge |
| Releasing Alt+Space fires `ptt_end` | ✅ Confirmed |
| Ctrl+Alt+J fires `mute_toggle` | ✅ Confirmed after key.vk fix |
| Status badge updates in real-time | ✅ Badge shows "listening" / "muted" / "idle" |
| No zombie processes after window close | ✅ Daemon thread + os._exit(0) on shutdown |
| Backend logs every hotkey event at INFO | ✅ All events logged via loguru |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. `backend/desktop/__main__.py` — launcher lives in the package

The original plan had `backend/desktop.py` as the PyWebView launcher alongside
`backend/desktop/` as the hotkeys package. Python cannot have both a `backend/desktop.py`
module and a `backend/desktop/` package simultaneously — the package takes priority and
the `.py` file becomes unreachable. The fix was to move the launcher into
`backend/desktop/__main__.py`. When a Python package contains `__main__.py`, running
`python -m backend.desktop` executes it directly. This is cleaner than the original
design because the launcher and the hotkeys module are now co-located in one package.

### 2. B2 queue pattern — asyncio.Queue + call_soon_threadsafe

pynput's `Listener` runs in a native OS thread managed by pynput. FastAPI's WebSocket
broadcast runs in uvicorn's asyncio event loop. These two cannot share state directly.
The bridge: `loop.call_soon_threadsafe(queue.put_nowait, event)`. This is the only
thread-safe way to submit work to an asyncio loop from outside it.

The queue (`asyncio.Queue(maxsize=100)`) decouples production from consumption. The
`_drain_events` coroutine reads from the queue in the event loop and calls
`ws_manager.broadcast()`. This same pattern is what Day 11's conversation state machine
and Day 16's amplitude data will reuse — they push different event shapes into the same
queue, and the drainer broadcasts them all.

`maxsize=100` caps the queue so pathological PTT-spamming can't consume unbounded memory.
At normal use, the queue depth stays near zero.

### 3. `app.state.drainer_task` — preventing silent GC cancellation

`asyncio.create_task()` schedules a coroutine but returns a `Task` object. If nothing
holds a strong reference to that `Task`, the garbage collector can cancel it silently.
The symptom: hotkey events are emitted (visible in logs), the queue fills, but no
WebSocket messages arrive at the client. Storing the task on `app.state.drainer_task`
keeps it alive for the lifespan of the server.

### 4. `key.vk` instead of `key.char` for J detection

pynput reports each key event with both a `char` attribute (the Unicode character the
keypress produces) and a `vk` attribute (the raw Windows Virtual Key code, unaffected by
modifiers). When Ctrl is held, `key.char` for the J key is `'\x0a'` (ASCII 10 — Ctrl+J
maps to the line-feed control character). Checking `key.char.lower() == 'j'` fails
silently. `key.vk == ord('J')` (74) is stable regardless of held modifiers.

This applies to **all** character keys in Ctrl combos. Any future hotkey that involves
Ctrl + a letter must use `key.vk`, not `key.char`.

### 5. `ws://127.0.0.1:8000` instead of `ws://localhost:8000`

Edge WebView2 (PyWebView's underlying engine on Windows) has a quirk: for WebSocket
connections, it resolves `localhost` to `::1` (IPv6 loopback) before trying `127.0.0.1`
(IPv4). uvicorn is bound to `127.0.0.1` only. The TCP connection to `::1:8000` fails with
`ERR_CONNECTION_REFUSED` because nothing is listening there.

For regular HTTP (`fetch('http://localhost:8000/...')`), the same address lookup
apparently prefers `127.0.0.1` first — which is why the Ping button worked while
WebSocket failed. Explicitly using `127.0.0.1` in `WS_VOICE_URL` bypasses the
resolution ambiguity entirely.

`API_BASE` still uses `http://localhost:8000` because HTTP was never broken. This
inconsistency is noted — if issues appear in future, change `API_BASE` to
`http://127.0.0.1:8000` too.

### 6. Reconnect loop in `useVoiceEvents`

The React app mounts and runs `useEffect` as soon as the page loads inside PyWebView. The
1-second `time.sleep` in the launcher is not enough for uvicorn to finish loading all
modules on first run (chromadb, google-genai imports take 2–5 seconds). Both React
StrictMode's double-invoke attempts fail with connection refused before the backend is
ready. Without a retry, the hook just sits permanently disconnected.

The reconnect loop: on `ws.onclose`, schedule `connect()` again after 1,000ms. A
`cancelled` flag (set on cleanup) stops the loop when the component unmounts. This also
handles the backend restarting mid-session, which will be important when Day 11 adds a
more complex startup sequence.

---

## 3. Problems faced and how they were handled

### P1 — `ModuleNotFoundError: No module named 'backend'` *(impact: medium, resolved)*

- **What:** Running `python backend/desktop.py` raised `ModuleNotFoundError` for
  `from backend.config.settings import get_settings`.
- **Cause:** Running a script with `python path/to/file.py` adds the script's directory
  (`backend/`) to `sys.path`, not the repo root. Intra-package imports (`from backend.*`)
  require the repo root on the path.
- **Handled:** Switched to `python -m backend.desktop`. The `-m` flag runs the code as
  a module, which places the current working directory (repo root) on `sys.path`.
- **Verified:** No import error. All `from backend.*` imports resolve.

### P2 — `backend/desktop/` package shadows `backend/desktop.py` *(impact: high, resolved)*

- **What:** `python -m backend.desktop` raised: `'backend.desktop' is a package and
  cannot be directly executed`.
- **Cause:** Both `backend/desktop.py` (the launcher) and `backend/desktop/` (the
  hotkeys package) existed. Python finds the package directory first and cannot run it
  as a module without a `__main__.py`.
- **Handled:** Created `backend/desktop/__main__.py` with the launcher code and deleted
  `backend/desktop.py`. Python now correctly runs `__main__.py` when invoked with
  `python -m backend.desktop`.
- **Verified:** Single command starts the full stack.

### P3 — `RuntimeError: Event loop is closed` on hotkey press *(impact: medium, resolved)*

- **What:** After uvicorn failed to bind (port conflict from a previous session), the
  hotkey listener kept running. Pressing Alt+Space triggered `_emit()`, which called
  `_loop.call_soon_threadsafe(...)` on a closed event loop, producing a traceback on
  every keypress.
- **Cause:** uvicorn failing to start closes its event loop, but pynput's Listener thread
  is independent and doesn't stop. The `_loop` reference in `hotkeys.py` now points to a
  closed loop.
- **Handled:** Added `if _loop.is_closed(): return` guard in `_emit()` before calling
  `call_soon_threadsafe`. Also killed the stale process holding port 8000 (`netstat -ano
  | findstr :8000` → `Stop-Process`).
- **Lesson:** Always kill port 8000 before relaunching. The guard means closed-loop
  errors are now silent rather than spammy.

### P4 — Ctrl+Alt+J not detected *(impact: medium, resolved)*

- **What:** Pressing Ctrl+Alt+J produced no `mute_toggle` event and no log line.
- **Cause:** The original detection used `key.char.lower() == 'j'`. With Ctrl held,
  pynput reports `key.char == '\x0a'` (Ctrl+J = ASCII line-feed). The condition was
  silently False on every press.
- **Handled:** Changed to `key.vk == ord('J')` (74). Virtual key codes are modifier-
  independent on Windows.
- **Verified:** `mute_toggle` events appear in logs immediately after fix.

### P5 — WebSocket `ERR_CONNECTION_REFUSED` inside PyWebView *(impact: high, resolved)*

- **What:** `useVoiceEvents` hook logged `net::ERR_CONNECTION_REFUSED` for
  `ws://127.0.0.1:8000/ws/voice`. The status badge never updated. HTTP to port 8000
  (the Ping button) worked fine.
- **Root cause (part 1 — IPv6):** `ws://localhost:8000` was the original URL. Edge
  WebView2 resolves `localhost` to `::1` for WebSocket. uvicorn only listens on
  `127.0.0.1`. Fixed by changing `WS_VOICE_URL` to `ws://127.0.0.1:8000/ws/voice`.
- **Root cause (part 2 — timing):** Even after the IPv4 fix, the error persisted.
  PyWebView's HMR WebSocket uses the same localhost-to-IPv6 path, so the config change
  didn't reach the running window (HMR was also broken). After a relaunch with the new
  code, `ERR_CONNECTION_REFUSED` still appeared because the React hook fires during
  page load — before uvicorn has finished importing heavy modules (chromadb, google-genai)
  and binding port 8000.
- **Diagnosis:** DevTools console WebSocket test (`new WebSocket('ws://127.0.0.1:8000/
  ws/voice')`) returned `OPEN` when run manually, proving the backend was reachable.
  The hook's failures were timing-only.
- **Handled:** Added reconnect loop to `useVoiceEvents`: on `ws.onclose`, retry after
  1,000ms. The hook now connects successfully on the second or third attempt once uvicorn
  is ready.
- **Verified:** `WS /ws/voice connected` appears in backend logs; status badge updates
  on Alt+Space.

---

## 4. Heads-up: downstream complications to watch

### The 1-second startup delay is fragile

`time.sleep(1.0)` in `__main__.py` is a heuristic. On cold start (first run after a
reboot or venv install), chromadb and google-genai imports can take 3–5 seconds. The
sleep expires before uvicorn is bound, the window opens to a backend that isn't ready,
and the reconnect loop in `useVoiceEvents` saves the day.

The reconnect loop is the real fix. But the sleep is still there, and Day 8 will
introduce audio capture that runs in its own thread. If the audio service also needs
the backend to be ready, it could race with startup.

**Watch for:** Day 8 audio capture starting before the backend's event loop is ready.
If `audio.start_recording()` is triggered by a hotkey event before the drainer task
is fully alive, it will try to emit events to an empty queue or a not-yet-wired system.
**Mitigation:** The drainer task being alive is the right gate. Consider adding an
`app.state.ready` flag set after lifespan startup completes.

### Vite HMR is broken inside PyWebView

Vite uses WebSocket for hot module replacement. Its HMR WebSocket connects to
`ws://localhost:5173/...`, which has the same IPv6 resolution issue inside Edge
WebView2. The PyWebView window never receives HMR updates.

**Consequence:** After any frontend code change, the PyWebView window must be closed
and relaunched to see the update. The Vite dev server does NOT need to be restarted —
just the window.

**Workaround:** During frontend development, test in a regular browser
(`http://localhost:5173`) where HMR works, then verify in PyWebView before committing.

**Possible fix (not done in v1):** PyWebView can be configured to pass additional
WebView2 browser arguments. `--host-resolver-rules="MAP localhost 127.0.0.1"` would
force all `localhost` resolutions to IPv4, fixing both the WS and HMR issues. Deferred
because the reconnect loop handles the WS case and HMR workaround is acceptable.

### `key.vk` is Windows-only

`pynput`'s `key.vk` attribute is a Windows Virtual Key code. On Linux/macOS, pynput
uses different key identification. Since this project is Windows-only, this is fine.
But if anyone tries to port Jarvis to macOS in Month 3+, every `key.vk` check will
need to be replaced with OS-appropriate equivalents.

### Drag bar blocks click-through on the top 32px strip

`-webkit-app-region: drag` on the drag bar means that area is handled by the native
window manager, not by JavaScript. Any future UI element placed in the top 32px of
the window must have `-webkit-app-region: no-drag` applied, or clicks on it will move
the window instead of triggering the element. The close button already has this; any
Day 17 settings panel button in the top strip will need it too.

### pynput Listener thread is not stopped on window close

`start_listener()` returns the `Listener` object, but `__main__.py` doesn't store it
or call `.stop()` on shutdown. The thread is `daemon=True` on the OS level (it inherits
daemon status from pynput's internal implementation), so it dies when the process exits.
But if future work tries to restart the listener without restarting the process (e.g.,
a "reload hotkeys" settings UI), it will create a second listener on top of the first.

**Mitigation for now:** The listener is started once at uvicorn startup and the process
exits when the window closes. No restart path exists yet.
**If Day 17+ adds a settings reload:** store the `Listener` on `app.state.hotkey_listener`
in the lifespan and call `.stop()` before creating a new one.

---

## 5. How to verify Day 7

```powershell
# 1. Start Vite dev server (separate terminal, keep it running)
cd frontend; npm run dev

# 2. Start the integrated launcher
python -m backend.desktop

# 3. Wait ~3 seconds for the reconnect loop to connect. Check logs:
Get-Content data/logs/jarvis.log -Tail 5
# Expected: "WS /ws/voice connected"

# 4. With any other window focused:
#    Hold Alt+Space → badge shows "listening", log shows "hotkey → ptt_start"
#    Release → badge shows "idle", log shows "hotkey → ptt_end"
#    Press Ctrl+Alt+J → badge shows "muted"
#    Press Ctrl+Alt+J again → badge shows "idle"

# 5. Drag the top bar → window moves
# 6. Click ✕ → process exits. Confirm in Task Manager: no leftover python.exe
```

---

## 6. Open items before Day 8

- [ ] Consider adding `app.state.ready = True` flag after lifespan startup completes,
      so Day 8's audio service can gate on it rather than assuming the backend is up.
- [ ] Document the "close and relaunch for frontend changes" workflow somewhere visible
      (README or `docs/setup.md`) so it's not surprising when HMR silently does nothing.

---

## 7. Commit log for this period

```
feat: pywebview shell - transparent always-on-top with global hotkeys

- Move PyWebView launcher into backend/desktop/__main__.py so python -m backend.desktop works
- Start FastAPI/uvicorn on a daemon thread before webview.start() blocks; single command starts everything
- Add tkinter-based screen size query; window placed at bottom-right corner
- Add backend/desktop/hotkeys.py: pynput Listener on OS thread, bridges to asyncio.Queue
  via loop.call_soon_threadsafe; guards closed loop
- Add ConnectionManager to backend/api/voice.py for WebSocket fan-out broadcast
- Wire asyncio.Queue and _drain_events task in main.py lifespan; drainer stored on
  app.state to prevent GC
- Add POST /shutdown to health.py for close button
- Fix Ctrl+Alt+J detection: use key.vk == ord(J) instead of key.char
- Fix WS_VOICE_URL to use 127.0.0.1: Edge WebView2 resolves localhost to IPv6
  for WebSocket but uvicorn binds IPv4 only
- Add reconnect loop to useVoiceEvents hook: retries every 1s on close
- Frontend: transparent drag bar, close button, cyan test circle, status badge
- Migrate main.py from deprecated on_event to lifespan context manager

docs: add Day 7 journal entry
```
