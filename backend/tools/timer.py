"""set_timer — schedule a background countdown that fires a Windows toast on completion."""

import asyncio

from loguru import logger
from plyer import notification

from backend.config.settings import get_settings
from backend.tools import registry

# Strong references to in-flight timer tasks. asyncio GC-cancels tasks that have
# no live reference — without this set, a timer silently never fires. The
# done-callback below discards finished tasks automatically.
_active_timers: set[asyncio.Task] = set()


@registry.register(
    name="set_timer",
    description=(
        "Set a countdown timer. Use this when the user asks to set a timer, alarm, "
        "reminder, or Pomodoro for a number of minutes. Always confirm the duration "
        "back to the user in your spoken reply. Duration may be fractional (0.5 = 30 seconds)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "minutes": {
                "type": "number",
                "description": "Timer duration in minutes. May be fractional (e.g. 0.5 for 30 seconds).",
            },
            "label": {
                "type": "string",
                "description": "Optional label shown in the toast, e.g. 'Pomodoro' or 'Tea'. Defaults to 'Timer'.",
            },
        },
        "required": ["minutes"],
    },
)
async def set_timer(minutes: float, label: str = "Timer") -> str | dict:
    """Validate, schedule, and return immediately. The actual timer runs as a background task."""
    s = get_settings()

    if minutes <= 0:
        return {"error": "Timer duration must be greater than zero.", "type": "ValidationError"}
    if minutes > s.timer_max_minutes:
        return {
            "error": f"Timer duration {minutes}m exceeds the maximum ({s.timer_max_minutes}m).",
            "type": "ValidationError",
        }

    # Create the background task and keep a strong reference to prevent GC cancellation.
    task = asyncio.create_task(_run_timer(minutes, label))
    _active_timers.add(task)
    task.add_done_callback(_active_timers.discard)

    logger.info("timer scheduled: {:.3g}m — '{}'", minutes, label)
    return f"Timer set for {minutes:g} minute(s): {label}."


async def _run_timer(minutes: float, label: str) -> None:
    """Sleep, then fire the toast (always) + WS event (always) + speak if IDLE (Decision C2)."""
    await asyncio.sleep(minutes * 60)

    s = get_settings()
    loop = asyncio.get_running_loop()

    # Toast always fires regardless of voice loop state — it's a visual notification.
    # plyer.notification.notify is synchronous and can raise on Windows, so it runs in
    # an executor. We catch and log but never let a toast failure kill the background task.
    try:
        await loop.run_in_executor(
            None,
            lambda: notification.notify(
                title="Jarvis",
                message=f"{label} — time's up",
                timeout=s.notification_timeout_seconds,
            ),
        )
        logger.info("timer fired: toast shown for '{}'", label)
    except Exception as e:
        logger.warning("timer toast failed for '{}': {}", label, e)

    # Broadcast timer_fired so the frontend can react (e.g. flash the blob).
    # Lazy local import mirrors the web_search / grounded_search pattern — avoids
    # a circular import at module load time (main.py imports this module during lifespan).
    try:
        from backend.api.voice import manager as ws_manager  # noqa: PLC0415
        await ws_manager.broadcast({"type": "timer_fired", "label": label})
    except Exception as e:
        logger.warning("timer_fired broadcast failed for '{}': {}", label, e)

    # Speak only when the voice loop is IDLE (Decision C2).
    # Read state under the lock, release, then speak — never hold the lock across TTS.
    if not s.timer_announce_on_idle:
        return

    try:
        from backend.main import app  # noqa: PLC0415
        from backend.models.voice import VoiceState  # noqa: PLC0415

        orchestrator = app.state.conversation
        tts = app.state.tts_service

        # Acquire the lock only to read the state — do not hold it across the TTS call.
        async with orchestrator._lock:
            is_idle = orchestrator.state == VoiceState.IDLE

        if is_idle:
            await tts.speak(f"{label} — time's up, sir.")
            logger.info("timer: spoke completion for '{}'", label)
        else:
            logger.info("timer: skipped speech for '{}' (state not IDLE)", label)
    except Exception as e:
        # Never let a speech failure propagate — the toast already fired.
        logger.warning("timer speech failed for '{}': {}", label, e)
