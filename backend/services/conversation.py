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
from backend.llm.base import LLMError, TextResponse
from backend.tools import registry
from backend.tools.registry import ToolNotFoundError, ToolSchemaError
from backend.llm.router import LLMRouter
from backend.memory import importance, sqlite_store, vector_store
from backend.models.voice import (
    AssistantMessageEvent,
    AudioDeviceRecoveredEvent,
    RecordingCapHitEvent,
    SpeakingEndedEvent,
    SpeakingFailedEvent,
    SpeakingStartedEvent,
    StateChangedEvent,
    VoiceState,
)
from backend.prompts.system_prompts import JARVIS_SYSTEM_PROMPT
from backend.voice.audio import AudioCaptureError, AudioRecorder
from backend.voice.cleanup import prune_recordings
from backend.voice.stt import STTError, STTService
from backend.voice.tts import TTSError, TTSService


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
        # Amplitude broadcast task — runs during LISTENING and SPEAKING only.
        # Cancelled by _transition() on every state change.
        self._amp_broadcast_task: asyncio.Task | None = None

        logger.info(f"conversation orchestrator initialized — state={self._state.value}")

    @property
    def state(self) -> VoiceState:
        return self._state

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

            try:
                self._recorder.start_recording()
            except AudioCaptureError as exc:
                # Device open failed — try rebuilding against the system default device.
                logger.warning(f"orchestrator: start_recording failed ({exc}), attempting rebuild")
                try:
                    self._rebuild_recorder()
                    self._recorder.start_recording()
                    # Rebuild succeeded: notify the UI before proceeding.
                    await self._broadcast(AudioDeviceRecoveredEvent().model_dump())
                    logger.info("orchestrator: recorder rebuilt on default device")
                except AudioCaptureError as exc2:
                    # Broadcast a toast before transitioning to ERROR so the user
                    # sees the classified message, not just the state badge change.
                    await self._broadcast({"type": "transcription_failed", "error": str(exc2)})
                    await self._handle_error(str(exc2))
                    return

            await self._transition(VoiceState.LISTENING)

    async def on_ptt_end(self) -> None:
        """PTT key up: stop recording and spawn the STT → LLM → TTS turn."""
        async with self._lock:
            # G1: only allowed in LISTENING; stray ptt_end (no matching start) is dropped.
            if self._state != VoiceState.LISTENING:
                logger.warning(f"orchestrator: ptt_end in {self._state.value} — ignored")
                return
            wav_bytes = await self._drain_recording()
        # Lock released. Spawn the task outside the lock so the lock window stays tight.
        if wav_bytes:
            self._inflight = asyncio.create_task(self._process_turn(wav_bytes))
            self._inflight.add_done_callback(self._on_turn_complete)

    async def on_recording_cap_hit(self) -> None:
        """30s PTT cap fired: drain the buffer, warn the UI, and dispatch the turn.

        Treated as an implicit ptt_end — the recorder has already stopped buffering.
        Dropped if the state is no longer LISTENING (e.g. user released PTT just
        before the cap notification arrived).
        """
        async with self._lock:
            if self._state != VoiceState.LISTENING:
                logger.warning(
                    f"orchestrator: recording_cap_hit in {self._state.value} — ignored"
                )
                return
            # Warn the UI before draining so the user sees feedback immediately.
            await self._broadcast(RecordingCapHitEvent().model_dump())
            wav_bytes = await self._drain_recording()
        # Lock released. Spawn as a task so this handler returns immediately.
        if wav_bytes:
            self._inflight = asyncio.create_task(self._process_turn(wav_bytes))
            self._inflight.add_done_callback(self._on_turn_complete)

    async def _drain_recording(self) -> bytes:
        """Stop the recorder, validate the buffer, transition to TRANSCRIBING.

        Must be called with self._lock already held.
        Returns wav_bytes if the turn should be dispatched; b"" otherwise
        (caller should not spawn a task on empty return).
        """
        loop = asyncio.get_running_loop()
        # stop_recording closes the PortAudio stream — run in executor to avoid blocking.
        # Raises AudioCaptureError if the callback stored a hardware fault mid-recording.
        try:
            wav_bytes = await loop.run_in_executor(None, self._recorder.stop_recording)
        except AudioCaptureError as exc:
            await self._broadcast({"type": "transcription_failed", "error": str(exc)})
            await self._handle_error(str(exc))
            return b""
        if not wav_bytes:
            # Press too short, or mic never opened — give explicit feedback.
            await self._broadcast({"type": "transcription_failed", "error": "I didn't hear anything."})
            await self._transition(VoiceState.IDLE)
            return b""
        # Defense: don't spawn a second task if one is somehow already running.
        if self._inflight and not self._inflight.done():
            logger.warning("orchestrator: drain attempted with inflight task — dropping")
            return b""
        await self._transition(VoiceState.TRANSCRIBING)
        return wav_bytes

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

    def _rebuild_recorder(self) -> None:
        """Replace self._recorder with a new instance on the system default device.

        Must be called with self._lock already held. Does NOT open a stream —
        the caller is responsible for calling start_recording() after rebuilding.
        Uses index=None so Windows picks the current default regardless of how
        device indices shifted after a disconnect/reconnect.
        Preserves the notify_cap_hit callable from the old recorder.
        """
        notify = self._recorder._notify_cap_hit
        self._recorder = AudioRecorder(
            sample_rate=self._settings.audio_sample_rate,
            channels=self._settings.audio_channels,
            dtype=self._settings.audio_dtype,
            device_index=None,
            max_seconds=self._settings.recording_max_seconds,
            notify_cap_hit=notify,
        )

    async def _transition(self, new_state: VoiceState) -> None:
        # MUST be called with self._lock already held.
        # G3: this is the ONLY place that mutates self._state.

        # Cancel the amplitude broadcast on every state change — only LISTENING
        # and SPEAKING have an active audio source to react to.
        # cancel() schedules CancelledError at the next asyncio.sleep(); no await needed.
        if self._amp_broadcast_task and not self._amp_broadcast_task.done():
            self._amp_broadcast_task.cancel()
        self._amp_broadcast_task = None

        prev = self._state
        self._state = new_state
        await self._broadcast(
            StateChangedEvent(
                state=new_state.value,      # type: ignore[arg-type]
                prev_state=prev.value,      # type: ignore[arg-type]
            ).model_dump()
        )
        logger.debug(f"orchestrator: {prev.value} → {new_state.value}")

        # Start a fresh broadcast task for audio-active states.
        # create_task() is safe inside the lock — it schedules the coroutine but
        # doesn't run it until the next event-loop tick after this method returns.
        if new_state == VoiceState.LISTENING:
            self._amp_broadcast_task = asyncio.create_task(
                self._broadcast_amplitude("mic")
            )
        elif new_state == VoiceState.SPEAKING:
            self._amp_broadcast_task = asyncio.create_task(
                self._broadcast_amplitude("tts")
            )

    async def _broadcast_amplitude(self, source: str) -> None:
        """Poll amplitude from the active source and broadcast at amplitude_broadcast_hz.

        Runs as a background task during LISTENING (source='mic') and SPEAKING
        (source='tts'). Cancelled by _transition() on every state change — CancelledError
        propagates naturally from asyncio.sleep() with no try/except needed.
        Not logged per-tick: 20Hz × a 30s turn = 600 lines per turn.
        """
        interval = 1.0 / self._settings.amplitude_broadcast_hz
        while True:
            value = (
                self._recorder.latest_amplitude
                if source == "mic"
                else self._tts.latest_amplitude
            )
            await self._broadcast({"type": "amplitude", "value": value, "source": source})
            await asyncio.sleep(interval)

    async def _handle_error(self, msg: str) -> None:
        """Transition to ERROR and schedule auto-recovery to IDLE after 3s.

        Must be called with self._lock already held.
        Auto-recovery task is stored on self._recovery_task to prevent GC.
        """
        # Enforce the lock contract at dev time — a violation here means state
        # would be mutated without serialisation, producing silent races.
        assert self._lock.locked(), "_handle_error must be called with self._lock held"
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

        with logger.contextualize(request_id=turn_id):
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

            # ── LLM + tool-call loop ──────────────────────────────────────────
            # Lazy import: keeps the Gemini SDK out of the module top level.
            # Only needed here — no other method touches Gemini types directly.
            from google.genai import types as _genai_types

            context = await self._build_context(project_id, stt_result.text)
            system_prompt = self._build_system_prompt(context)

            # Build the multi-turn contents list. Context is already in system_prompt;
            # the user content is just the raw transcription text.
            contents: list = [
                _genai_types.Content(
                    role="user",
                    parts=[_genai_types.Part(text=stt_result.text)],
                )
            ]
            active_tools = registry.gemini_function_schemas() if len(registry) > 0 else None
            final_text: str | None = None
            final_response = None  # last LLMResponse — used for provider/model in persist

            for _iteration in range(self._settings.max_tool_calls):
                # MUTED re-check before each LLM call (voice-pipeline SKILL.md rule).
                # Slow tools (PDF, web search) are exactly when the user hits mute.
                async with self._lock:
                    if self._state == VoiceState.MUTED:
                        return

                try:
                    llm_response = await self._llm.generate(
                        contents,
                        system_prompt=system_prompt,
                        tools=active_tools,
                    )
                except LLMError:
                    async with self._lock:
                        if self._state == VoiceState.MUTED:
                            return
                        await self._handle_error("Couldn't get a response. Please try again.")
                    return

                final_response = llm_response

                if isinstance(llm_response, TextResponse):
                    final_text = llm_response.text
                    break

                # Tool call — execute and loop back for the next LLM call.
                fc_name = llm_response.tool_name
                fc_args = llm_response.tool_args

                # Preserve the model's content object EXACTLY — Gemini requires the
                # full object in the next call. Reconstructing from strings causes a
                # Gemini API error. (Day 19 design §5 — the #1 gotcha in this loop.)
                contents.append(llm_response.raw.candidates[0].content)

                logger.info(f"tool_call iter={_iteration}: {fc_name}({fc_args})")

                try:
                    tool_result = await registry.execute(fc_name, fc_args)
                except (ToolNotFoundError, ToolSchemaError):
                    raise  # hard error — propagates to _process_turn → _handle_error

                contents.append(_genai_types.Content(
                    role="user",
                    parts=[_genai_types.Part.from_function_response(
                        name=fc_name,
                        response={"result": tool_result},
                    )],
                ))

                logger.info(f"tool_result: {fc_name} → {tool_result!r}")

                # When the active project changes, push a project_changed event so
                # the UI status bar updates without polling. Only broadcast on success
                # (str result); a soft error returns a dict and means the switch failed.
                if fc_name == "set_active_project" and isinstance(tool_result, str):
                    try:
                        active = sqlite_store.get_active_project()
                        await self._broadcast({"type": "project_changed", "name": active["name"]})
                    except Exception as exc:
                        logger.warning(f"orchestrator: project_changed broadcast failed: {exc}")

            else:
                # Loop exhausted without a text response — hit max_tool_calls (Q8).
                # Force one final text-only call so the user gets some reply.
                logger.warning(
                    f"tool-call loop hit max_tool_calls={self._settings.max_tool_calls}"
                )
                async with self._lock:
                    if self._state == VoiceState.MUTED:
                        return
                try:
                    fallback_r = await self._llm.generate(
                        contents + [_genai_types.Content(
                            role="user",
                            parts=[_genai_types.Part(
                                text="[max tool calls reached; please reply in plain text only]"
                            )],
                        )],
                        system_prompt=system_prompt,
                        tools=None,
                    )
                    if isinstance(fallback_r, TextResponse):
                        final_text = fallback_r.text
                        final_response = fallback_r
                    else:
                        final_text = "I'm not sure how to help with that."
                except LLMError:
                    final_text = "I'm not sure how to help with that."

            assistant_text = final_text  # guaranteed non-None: set in break or else

            # ── Persist ───────────────────────────────────────────────────────
            # SQLite is sync and fast for single-user; no executor needed here.
            await self._persist_turn(
                project_id=project_id,
                conversation_id=conversation_id,
                user_text=stt_result.text,
                assistant_text=assistant_text,
                provider=final_response.provider if final_response else "unknown",
                model=final_response.model if final_response else "unknown",
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

        # Non-fatal cleanup: prune WAV files older than the configured age.
        # Runs in executor (sync file I/O); errors are logged and swallowed.
        try:
            await loop.run_in_executor(
                None,
                prune_recordings,
                recordings_dir,
                self._settings.recordings_max_age_days,
            )
        except Exception as exc:
            logger.warning(f"orchestrator: recordings cleanup failed (non-fatal): {exc}")

        return path

    def _build_system_prompt(self, context: str) -> str:
        """Append memory context to the JARVIS system prompt when available."""
        if not context:
            return JARVIS_SYSTEM_PROMPT
        return (
            JARVIS_SYSTEM_PROMPT
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
                # Mirror to SQLite memory table so voice-loop turns are SQL-queryable.
                # chroma_id is passed so the two stores stay linked; save_memory is sync + fast.
                sqlite_store.save_memory(
                    text=combined,
                    project_id=project_id,
                    importance=score,
                    chroma_id=chroma_id,
                )
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
        if self._amp_broadcast_task and not self._amp_broadcast_task.done():
            self._amp_broadcast_task.cancel()
        logger.info("conversation orchestrator closed")
