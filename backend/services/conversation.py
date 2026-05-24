"""ConversationOrchestrator — the voice loop state machine.

Singleton on app.state.conversation. Owns the full PTT → STT → LLM → TTS pipeline.
All public methods are async and acquire self._lock before mutating state.
Side effects (STT, LLM, TTS) run *outside* the lock so the event loop stays free
during network I/O. Re-acquire the lock to mutate state again after I/O completes.

State machine:
    IDLE → LISTENING → TRANSCRIBING → THINKING → SPEAKING → IDLE
    Any non-muted state  → MUTED (mute_toggle)
    MUTED                → IDLE  (mute_toggle)
    Any state            → ERROR (on failure) → IDLE (auto-recover, 3s)
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from backend.config.settings import Settings
from backend.llm.base import LLMError
from backend.llm.router import LLMRouter
from backend.memory import importance, sqlite_store, vector_store
from backend.models.voice import (
    AssistantMessageEvent,
    SpeakingEndedEvent,
    SpeakingFailedEvent,
    SpeakingStartedEvent,
    StateChangedEvent,
    VoiceState,
)
from backend.voice.audio import AudioRecorder
from backend.voice.stt import STTError, STTService
from backend.voice.tts import TTSError, TTSService

# Speech-optimised: no markdown, no bullets — the user hears the response.
_BASE_SYSTEM_PROMPT = (
    "You are J.A.R.V.I.S., an intelligent research and productivity assistant. "
    "The user will hear your response as speech, so keep replies concise and "
    "conversational. Avoid markdown, bullet points, and long lists. "
    "Answer questions directly."
)


class ConversationOrchestrator:
    # broadcast is injected (not imported) so the orchestrator stays testable
    # and the WS layer stays in api/voice.py where it belongs.
    def __init__(
        self,
        recorder: AudioRecorder,
        stt: STTService,
        llm: LLMRouter,
        tts: TTSService,
        broadcast: Callable[[dict], Awaitable[None]],
        settings: Settings,
    ) -> None:
        self._recorder = recorder
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._broadcast = broadcast
        self._settings = settings

        self._state = VoiceState.IDLE
        # Lock serialises all state mutations. Held only for state reads/writes;
        # released across every await that does real I/O (STT, LLM, TTS).
        self._lock = asyncio.Lock()
        # At most one turn in flight at any time. Tracked so mute can cancel it.
        self._inflight: asyncio.Task | None = None
        # Reference to the auto-recovery task so GC can't silently cancel it.
        self._recovery_task: asyncio.Task | None = None

        logger.info(f"conversation orchestrator initialized — state={self._state.value}")

    # ------------------------------------------------------------------
    # Public event handlers — called by the WS dispatcher (api/voice.py)
    # ------------------------------------------------------------------

    async def on_ptt_start(self) -> None:
        """PTT key down: begin recording if idle. Ignored in every other state."""
        async with self._lock:
            # G1: only allowed in IDLE; second press during SPEAKING/THINKING is dropped.
            if self._state != VoiceState.IDLE:
                logger.warning(f"orchestrator: ptt_start in {self._state.value} — ignored")
                return
            # start_recording opens a PortAudio stream — fast, no executor needed.
            self._recorder.start_recording()
            await self._transition(VoiceState.LISTENING)

    async def on_ptt_end(self) -> None:
        """PTT key up: stop recording and spawn the STT → LLM → TTS turn."""
        async with self._lock:
            # G1: only allowed in LISTENING; stray ptt_end (no matching start) is dropped.
            if self._state != VoiceState.LISTENING:
                logger.warning(f"orchestrator: ptt_end in {self._state.value} — ignored")
                return
            loop = asyncio.get_running_loop()
            # stop_recording closes the PortAudio stream — run in executor to avoid blocking.
            wav_bytes = await loop.run_in_executor(None, self._recorder.stop_recording)
            if not wav_bytes:
                # Press too short, or mic never opened — give explicit feedback.
                await self._broadcast({"type": "transcription_failed", "error": "I didn't hear anything."})
                await self._transition(VoiceState.IDLE)
                return
            # Defense: don't spawn a second task if one is somehow already running.
            if self._inflight and not self._inflight.done():
                logger.warning("orchestrator: ptt_end with inflight task — dropping new turn")
                return
            await self._transition(VoiceState.TRANSCRIBING)
        # Lock released. Spawn the processing task so on_ptt_end returns immediately.
        # The task reference is retained on self._inflight so mute can cancel it.
        self._inflight = asyncio.create_task(self._process_turn(wav_bytes))
        self._inflight.add_done_callback(self._on_turn_complete)

    async def on_mute_toggle(self) -> None:
        """Ctrl+Alt+J: mute any active state; unmute from muted.

        Pattern: capture pre-transition state + inflight reference while holding the lock,
        do the state mutation, then release the lock before running slow side effects
        (executor calls, task cancellation). This keeps the lock window tight.
        """
        inflight_to_cancel: asyncio.Task | None = None
        do_stop_recorder = False
        do_cancel_playback = False
        prev_state: VoiceState | None = None

        async with self._lock:
            current = self._state

            if current == VoiceState.MUTED:
                # Second press: unmute back to idle.
                await self._transition(VoiceState.IDLE)
                return

            if current == VoiceState.ERROR:
                # Cancel pending auto-recovery so we stay muted instead of snapping to idle.
                if self._recovery_task and not self._recovery_task.done():
                    self._recovery_task.cancel()
                    self._recovery_task = None
                await self._transition(VoiceState.MUTED)
                return

            # All other active states → MUTED first, side effects after lock release.
            prev_state = current
            await self._transition(VoiceState.MUTED)

            if current == VoiceState.LISTENING:
                do_stop_recorder = True
            elif current in (VoiceState.TRANSCRIBING, VoiceState.THINKING):
                inflight_to_cancel = self._inflight
            elif current == VoiceState.SPEAKING:
                # Stop audio first; cancel task second. Order matters for user perception.
                do_cancel_playback = True
                inflight_to_cancel = self._inflight

        # Lock released — run side effects without blocking state mutation for other events.

        if do_stop_recorder:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._recorder.stop_recording)
            logger.info("orchestrator: recording discarded (muted while listening)")

        if do_cancel_playback:
            # sd.stop() returns immediately once PortAudio drains the buffer;
            # the _play_sync executor call unblocks and speak() returns.
            await self._tts.cancel_playback()
            logger.info("orchestrator: playback stopped (muted while speaking)")

        if inflight_to_cancel and not inflight_to_cancel.done():
            inflight_to_cancel.cancel()
            logger.info(f"orchestrator: in-flight task cancelled (was {prev_state.value if prev_state else '?'})")

    # ------------------------------------------------------------------
    # Internal — state machine
    # ------------------------------------------------------------------

    async def _transition(self, new_state: VoiceState) -> None:
        # MUST be called with self._lock already held.
        # G3: this is the ONLY place that mutates self._state.
        prev = self._state
        self._state = new_state
        await self._broadcast(
            StateChangedEvent(
                state=new_state.value,      # type: ignore[arg-type]
                prev_state=prev.value,      # type: ignore[arg-type]
            ).model_dump()
        )
        logger.debug(f"orchestrator: {prev.value} → {new_state.value}")

    async def _handle_error(self, msg: str) -> None:
        """Transition to ERROR and schedule auto-recovery to IDLE after 3s.

        Must be called with self._lock already held.
        Auto-recovery task is stored on self._recovery_task to prevent GC.
        """
        await self._transition(VoiceState.ERROR)
        logger.warning(f"orchestrator: error — {msg}")
        # G4: schedule auto-recovery; don't await (would hold the lock for 3s).
        self._recovery_task = asyncio.create_task(self._auto_recover())

    async def _auto_recover(self) -> None:
        """Wait error_recovery_seconds then return to IDLE. Spawned as a task."""
        await asyncio.sleep(self._settings.error_recovery_seconds)
        async with self._lock:
            if self._state == VoiceState.ERROR:
                await self._transition(VoiceState.IDLE)
                logger.info("orchestrator: auto-recovered to idle")

    def _on_turn_complete(self, task: asyncio.Task) -> None:
        """Done callback on _inflight. Clears the reference; logs any surprise exception."""
        self._inflight = None
        if not task.cancelled() and (exc := task.exception()):
            logger.warning(f"orchestrator: _process_turn raised unexpected: {exc}")

    # ------------------------------------------------------------------
    # Internal — pipeline
    # ------------------------------------------------------------------

    async def _process_turn(self, wav_bytes: bytes) -> None:
        """Full STT → LLM → TTS pipeline for one voice turn. Runs as a Task.

        CancelledError re-raises so the task is properly marked cancelled.
        Any other unhandled exception transitions to ERROR.
        """
        try:
            await self._run_pipeline(wav_bytes)
        except asyncio.CancelledError:
            # Mute toggle cancelled this task. Mute handler already set state to MUTED.
            # Do NOT transition or broadcast — just re-raise.
            logger.debug("orchestrator: turn cancelled (muted mid-flight)")
            raise
        except Exception as exc:
            logger.exception(f"orchestrator: unhandled pipeline error: {exc}")
            async with self._lock:
                if self._state not in (VoiceState.MUTED, VoiceState.IDLE):
                    await self._handle_error("Internal error. Please try again.")

    async def _run_pipeline(self, wav_bytes: bytes) -> None:
        """The actual PTT → STT → LLM → TTS sequence."""

        # ── Preamble: project + conversation ──────────────────────────────
        project = sqlite_store.get_active_project()
        project_id: int = project["id"]
        conversation_id = sqlite_store.get_or_create_session_conversation(project_id)
        turn_id = uuid.uuid4().hex[:12]

        # ── STT ───────────────────────────────────────────────────────────
        # STTService takes a Path, not bytes — save to disk first.
        audio_path = await self._save_recording(wav_bytes)
        try:
            stt_result = await self._stt.transcribe(audio_path)
        except STTError as exc:
            async with self._lock:
                if self._state == VoiceState.MUTED:
                    return
                await self._broadcast({"type": "transcription_failed", "error": str(exc)})
                await self._handle_error(str(exc))
            return

        async with self._lock:
            if self._state == VoiceState.MUTED:
                return
            await self._broadcast({
                "type": "transcription_complete",
                "text": stt_result.text,
                "latency_ms": stt_result.latency_ms,
            })
            await self._transition(VoiceState.THINKING)

        # ── LLM ───────────────────────────────────────────────────────────
        context = await self._build_context(project_id, stt_result.text)
        system_prompt = self._build_system_prompt(context)
        try:
            llm_response = await self._llm.generate(stt_result.text, system_prompt=system_prompt)
        except LLMError:
            async with self._lock:
                if self._state == VoiceState.MUTED:
                    return
                await self._handle_error("Couldn't get a response. Please try again.")
            return

        assistant_text = llm_response.text

        # ── Persist ───────────────────────────────────────────────────────
        # SQLite is sync and fast for single-user; no executor needed here.
        await self._persist_turn(
            project_id=project_id,
            conversation_id=conversation_id,
            user_text=stt_result.text,
            assistant_text=assistant_text,
            provider=llm_response.provider,
            model=llm_response.model,
        )

        # ── Broadcast + transition to SPEAKING ────────────────────────────
        async with self._lock:
            if self._state == VoiceState.MUTED:
                return
            await self._broadcast(
                AssistantMessageEvent(text=assistant_text, turn_id=turn_id).model_dump()
            )
            await self._transition(VoiceState.SPEAKING)

        # speaking_started is broadcast outside the lock — it's a notification, not a mutation.
        await self._broadcast(SpeakingStartedEvent(turn_id=turn_id).model_dump())

        # ── TTS ───────────────────────────────────────────────────────────
        try:
            await self._tts.speak(assistant_text)
        except TTSError as exc:
            async with self._lock:
                if self._state == VoiceState.MUTED:
                    return
                await self._broadcast(
                    SpeakingFailedEvent(reason=str(exc), turn_id=turn_id).model_dump()
                )
                await self._handle_error(str(exc))
            return

        # ── Done ──────────────────────────────────────────────────────────
        async with self._lock:
            # Don't clobber MUTED if the user toggled mute while audio was playing.
            if self._state == VoiceState.MUTED:
                return
            await self._broadcast(SpeakingEndedEvent(turn_id=turn_id).model_dump())
            await self._transition(VoiceState.IDLE)

    async def _save_recording(self, wav_bytes: bytes) -> Path:
        """Write WAV bytes to data/recordings/ and return the Path for STTService."""
        recordings_dir = self._settings.recordings_dir
        recordings_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = recordings_dir / f"{timestamp}.wav"
        loop = asyncio.get_running_loop()
        # File write is blocking I/O — run in executor to keep the loop free.
        await loop.run_in_executor(None, path.write_bytes, wav_bytes)
        logger.debug(f"orchestrator: recording saved → {path.name}")
        return path

    def _build_system_prompt(self, context: str) -> str:
        """Append memory context to the base system prompt when available."""
        if not context:
            return _BASE_SYSTEM_PROMPT
        return (
            _BASE_SYSTEM_PROMPT
            + "\n\nYou have access to recent conversation and relevant past notes "
              "from the user's active project. Use them when they help; ignore them "
              "when they don't.\n\n" + context
        )

    async def _build_context(self, project_id: int, user_query: str) -> str:
        """Hybrid recency + semantic context for the LLM prompt.

        Combines:
        - Last N messages from SQLite (short-term continuity — handles "what did I just say?")
        - Top-k ChromaDB semantic hits (long-term recall — handles "what did we conclude last week?")

        Deduplication: a recent message that's also a top semantic hit is dropped from
        the semantic list so it only appears once (in the recency section).
        Char cap prevents stuffing the LLM context beyond useful limits.
        """
        # Recency: pull last N messages across all conversations in this project.
        recent = sqlite_store.get_recent_project_messages(
            project_id, limit=self._settings.recent_messages_limit
        )
        # Semantic: top-k embeddings closest to the user's query.
        # Returns [] if ChromaDB collection is empty (no messages stored yet).
        relevant_texts = await vector_store.search(
            user_query, project_id, k=self._settings.semantic_k
        )

        # Dedup: don't repeat a message verbatim in both sections.
        recent_contents = {m["content"] for m in recent}
        relevant_texts = [t for t in relevant_texts if t not in recent_contents]

        blocks: list[str] = []
        if recent:
            lines = "\n".join(f"  {m['role']}: {m['content']}" for m in recent)
            blocks.append(f"Recent conversation:\n{lines}")
        if relevant_texts:
            lines = "\n".join(f"  - {t}" for t in relevant_texts)
            blocks.append(f"Relevant past notes:\n{lines}")

        context = "\n\n".join(blocks)

        # Crude char-based token cap; replace with tiktoken in Day 20 when tool
        # schemas need accurate counting. len/4 ≈ tokens is a conservative estimate.
        if len(context) > self._settings.context_char_cap:
            context = context[: self._settings.context_char_cap] + "\n...[truncated]"

        return context

    async def _persist_turn(
        self,
        project_id: int,
        conversation_id: int,
        user_text: str,
        assistant_text: str,
        provider: str,
        model: str,
    ) -> None:
        """Save user + assistant messages to SQLite; write to ChromaDB if importance >= threshold.

        Importance scoring makes a second LLM call — wrapped in try/except so a
        scorer failure never breaks the voice loop. The messages are still saved
        to SQLite even if ChromaDB storage fails.
        """
        sqlite_store.save_message(conversation_id, project_id, "user", user_text)
        sqlite_store.save_message(
            conversation_id, project_id, "assistant", assistant_text, provider, model
        )
        logger.debug(f"orchestrator: turn persisted to SQLite (conv={conversation_id})")

        # Score the combined turn once; apply to both halves together.
        # Paired scoring is cheaper (one LLM call) and semantically correct —
        # the user's question and the assistant's answer belong to the same memory unit.
        try:
            combined = f"User: {user_text}\nAssistant: {assistant_text}"
            score = await importance.score(combined)
            logger.debug(f"orchestrator: importance score = {score}")
            if score >= self._settings.importance_threshold:
                chroma_id = await vector_store.add(
                    text=combined,
                    project_id=project_id,
                    metadata={"importance": score, "provider": provider},
                )
                logger.debug(f"orchestrator: stored in ChromaDB (id={chroma_id}, score={score})")
        except Exception as exc:
            # Non-fatal: memory storage failure must never break the voice loop.
            logger.warning(f"orchestrator: memory storage failed (non-fatal): {exc}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Cancel any in-flight task and wait for it. Called from lifespan shutdown."""
        # G5: cancel inflight and await with return_exceptions so shutdown never hangs.
        if self._inflight and not self._inflight.done():
            self._inflight.cancel()
            try:
                await self._inflight
            except (asyncio.CancelledError, Exception):
                pass
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
        logger.info("conversation orchestrator closed")
