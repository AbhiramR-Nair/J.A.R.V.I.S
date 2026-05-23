"""
Global hotkey listener using pynput.

Runs pynput's Listener on a native OS thread (pynput's own thread model).
Events are handed off to the asyncio event loop via loop.call_soon_threadsafe —
the only thread-safe way to enqueue items from a non-asyncio thread.

Wired hotkeys:
  Alt + Space          → ptt_start (press) / ptt_end (release)
  Ctrl + Alt + J       → mute_toggle

Decision notes (Day 7):
  - suppress=False: pynput's suppress is all-or-nothing on the Listener level,
    not per-combo. Suppressing globally breaks too many things. Accept the
    Windows system-menu flash on Alt+Space; revisit on Day 13 buffer if annoying.
  - B2 queue pattern: hotkey thread → asyncio.Queue → broadcaster task.
    Same event bus that Day 11 (conversation state machine) will extend.
"""

import asyncio
from typing import Optional

from loguru import logger
from pynput import keyboard

# Modifier + PTT state. pynput fires one event per key, so we track held
# modifiers manually to detect combos like Alt+Space.
_state: dict[str, bool] = {
    "alt": False,
    "ctrl": False,
    "ptt_active": False,  # True between ptt_start and ptt_end; prevents repeat-fire
}

# Injected by main.py at startup via init(). Kept as module-level so the
# pynput callbacks (which run in pynput's thread) can reach them without
# any shared mutable state beyond this module.
_loop: Optional[asyncio.AbstractEventLoop] = None
_event_queue: Optional[asyncio.Queue] = None  # type: ignore[type-arg]


def init(loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[dict]") -> None:
    """Inject the running asyncio loop and event queue from main.py startup.
    Must be called before start_listener(), or early hotkey events will be dropped."""
    global _loop, _event_queue
    _loop = loop
    _event_queue = queue


def _emit(event_type: str) -> None:
    """Thread-safe push of a hotkey event into the asyncio queue.
    call_soon_threadsafe is the correct bridge from a non-asyncio thread to
    an asyncio.Queue — calling queue.put_nowait directly from another thread
    is not safe."""
    if _loop is None or _event_queue is None:
        logger.warning(f"hotkey event '{event_type}' dropped: loop/queue not initialised")
        return
    if _loop.is_closed():
        # Backend shut down but listener thread is still alive (e.g. uvicorn failed
        # to bind). Silently drop — nothing useful can be done with the event.
        return
    _loop.call_soon_threadsafe(_event_queue.put_nowait, {"type": event_type})
    logger.info(f"hotkey → {event_type}")


def _on_press(key: keyboard.Key) -> None:
    # Wrap the entire callback: pynput silently swallows exceptions in callbacks,
    # so without this try/except a bug here produces zero log output.
    try:
        # Track modifier state
        if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
            _state["alt"] = True
            return
        if key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            _state["ctrl"] = True
            return

        # Alt + Space → ptt_start (guard with ptt_active to prevent repeat-fire
        # while the key is held down — OS sends repeated press events on hold)
        if key == keyboard.Key.space and _state["alt"] and not _state["ptt_active"]:
            _state["ptt_active"] = True
            _emit("ptt_start")
            return

        # Ctrl + Alt + J → mute_toggle.
        # Must use key.vk (virtual key code) rather than key.char because when
        # Ctrl is held, pynput reports key.char as the control character '\x0a'
        # (Ctrl+J = ASCII line-feed) rather than 'j'. key.vk is unaffected by
        # modifiers and is always ord('J') = 74 for the J key on Windows.
        if (
            _state["ctrl"]
            and _state["alt"]
            and hasattr(key, "vk")
            and key.vk == ord("J")
        ):
            _emit("mute_toggle")

    except Exception:
        logger.exception("hotkey _on_press error")


def _on_release(key: keyboard.Key) -> None:
    try:
        # Alt released while PTT is active → end PTT (user let go of the modifier)
        if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
            _state["alt"] = False
            if _state["ptt_active"]:
                _state["ptt_active"] = False
                _emit("ptt_end")
            return

        if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            _state["ctrl"] = False
            return

        # Space released while PTT is active → end PTT
        if key == keyboard.Key.space and _state["ptt_active"]:
            _state["ptt_active"] = False
            _emit("ptt_end")

    except Exception:
        logger.exception("hotkey _on_release error")


def start_listener() -> keyboard.Listener:
    """Start the pynput Listener in its own OS thread and return it.
    Caller (main.py) must hold the reference and call .stop() on shutdown,
    otherwise the thread outlives the process and leaks."""
    listener = keyboard.Listener(
        on_press=_on_press,
        on_release=_on_release,
        suppress=False,  # all-or-nothing on Listener level; per-combo suppression
                         # requires Win32 low-level hook — deferred to Day 13 buffer
    )
    listener.start()
    logger.info("global hotkey listener started (Alt+Space PTT, Ctrl+Alt+J mute)")
    return listener
