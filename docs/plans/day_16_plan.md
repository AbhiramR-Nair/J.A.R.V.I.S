# Day 16 Plan — Audio Reactivity

**Week 3, Day 2.** The orb gains hearing. Mic and TTS amplitude drive subtle motion in the blob — color bloom, calmer conic turn when listening, faster conic + breathing pulse when speaking.

---

## Goal for the day

By end of day, the blob visibly responds to sound — quietly during user speech (mic input), more actively during assistant speech (TTS output). The response is smoothed and tasteful (no jitter, no seizures), respects the per-state asymmetry decided in design (listening ≠ speaking), and stays at zero during thinking/idle/muted/error.

Implementation has three legs: backend amplitude computation and broadcast, frontend amplitude plumbing as a non-queued side channel, and `Blob.tsx` reading amplitude each frame to modulate the visuals defined in `BlobStates.ts`.

---

## Reactivity spec (locked from Day 15 prototyping)

Coefficients are multipliers on the smoothed amplitude (range 0.0–1.0). Zero means the state ignores that channel.

| Knob | idle | listening | thinking | speaking | muted | error |
|---|---|---|---|---|---|---|
| `pulseK` (scale boost) | 0 | 0 | 0 | **0.05** | 0 | 0 |
| `conicSpeedK` (rotation boost) | 0 | **0.6** | 0 | **2.8** | 0 | 0 |
| `colorAlphaK` (radial alpha boost) | 0 | **1.2** | 0 | **1.2** | 0 | 0 |
| `conicSaturateK` (conic saturation boost) | 0 | **0.7** | 0 | **0.7** | 0 | 0 |
| `highlightSizeK` (specular size boost) | 0 | **0.3** | 0 | **0.3** | 0 | 0 |
| `highlightAlphaK` (specular alpha boost) | 0 | **0.25** | 0 | **0.25** | 0 | 0 |

**Why thinking has all zeros:** there is no sound to react to (mic closed, TTS not yet playing). Reacting would invent a signal that doesn't exist and break the language ("color bloom = I hear sound" must stay literal).

**Why idle/muted/error have all zeros:** same reason — no audio source is active.

**Smoothing math (frontend):** exponential moving average with asymmetric attack/decay.
- Attack k = 0.35 (faster ramp up so the orb feels responsive)
- Decay k = 0.12 (slower fade so individual syllables don't strobe)

---

## Pre-flight (morning ritual, ~45 min)

### PF-1. CPU optimization carryover from Day 15

Day 15 closed with `msedgewebview2.exe` at ~13% in idle, over the 10% target. Cause: 5 separate `element.style.*` writes per element per frame in the rAF loop, each triggering a style recalculation.

**Fix:** batch all per-element writes into a single `element.style.cssText = ...` template literal per frame. One write per element per frame instead of 5.

**Acceptance:** idle CPU on the WebView process drops under 10%. Record the actual number in `docs/journal.md`. If still over after batching:
- Drop conic blur from 28px to 24px (most expensive CSS filter on the compositor path)
- If still over: drop the rAF loop from running every frame to every 2nd frame (~30fps). Acceptable given the slow nature of the motion. Hold this in reserve.

**Time-box: 20 minutes.** If you can't get under 10% by then, leave the journal note and move on — Day 16 substance matters more than 3% of CPU.

### PF-2. Palette tuning pass with fresh eyes

Open the dev cycler. Walk through all 6 states. Note anything that feels off after a night's rest. Adjust values in `BlobStates.ts` **only** — no component changes. Common things to look for:

- `listening` palette indistinguishable from `idle`?
- `thinking` palette too purple, not contemplative enough?
- `error` red too aggressive, or not aggressive enough?
- `muted` opacity right (40%) or want more / less ghostly?

**Time-box: 25 minutes.** Don't go past this. The Day 16 substance is where the day's value comes from.

---

## Architecture decision: amplitude bypasses the event queue

This is the single most important design choice in Day 16 and deserves an explicit rationale.

The existing `useVoiceEvents` hook drains events one at a time through a reducer queue (the Day 11 refactor). That pattern is correct for state events — every `state_changed`, every `assistant_message`, every `speaking_failed` must fire exactly once and be processed in order.

Amplitude is the **opposite** kind of signal:
- High frequency (~20 Hz vs. the few-per-turn rate of state events)
- Latest value wins — a 50ms-old amplitude is useless
- No semantic meaning if missed — losing one out of every five is invisible
- Must not block the queue from draining (a 30-event amplitude backlog would delay a `speaking_ended` event by 1.5s)

So amplitude goes through a parallel side channel: a `useRef` updated directly in the WebSocket `onmessage` handler, never dispatched to the reducer. The Blob's rAF loop reads `amplitudeRef.current` every frame.

This is the cleaner architecture — state events through the verified ordered queue, amplitude through a fire-and-forget ref. Both flow through one WebSocket connection but are dispatched into different React patterns based on what kind of signal they are.

---

## Tasks

### T-1. Backend: mic amplitude in `AudioRecorder`

**File:** `backend/voice/audio.py`

The sounddevice `InputStream` callback already runs every ~50ms while recording (it's where the WAV buffer is built). Extend it to also compute amplitude:

```python
# Per chunk, RMS of normalized float samples gives a good loudness proxy.
# Normalize against a calibration_max (set in settings; default 0.3 for typical
# speaking volume on built-in laptop mics). Clamp to [0, 1].
def _callback(self, indata, frames, time_info, status):
    # ... existing buffer write ...
    rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
    normalized = min(rms / settings.mic_calibration_max, 1.0)
    self._latest_amplitude = normalized   # plain float; reads are atomic
```

**Settings additions in `backend/config/settings.py`:**
- `mic_calibration_max: float = 0.3` — what counts as "loud"; lower = more sensitive
- `amplitude_broadcast_hz: int = 20` — broadcast cadence
- `tts_calibration_max: float = 0.5` — TTS levels run hotter than mic

Expose `latest_amplitude` as a property on `AudioRecorder`. The orchestrator reads it from a background task (see T-3).

### T-2. Backend: TTS amplitude tracking

**File:** `backend/voice/tts.py`

Currently `TTSService.speak()` reads Piper's PCM output into a numpy array and plays it with `sd.play(arr, samplerate=...)`. That's a single fire-and-forget call with no per-chunk visibility.

Refactor to use `sd.OutputStream` with a pull callback. The callback computes amplitude per chunk and stashes it on the service:

```python
# An OutputStream callback receives 'outdata' to fill and is called by sounddevice
# at a rate determined by blocksize. blocksize=int(sample_rate * 0.05) gives ~50ms
# chunks, matching the 20Hz amplitude broadcast cadence.
def _make_callback(audio: np.ndarray, service):
    cursor = 0
    def _cb(outdata, frames, time_info, status):
        nonlocal cursor
        end = cursor + frames
        chunk = audio[cursor:end]
        if len(chunk) < frames:
            outdata[:len(chunk)] = chunk.reshape(-1, 1)
            outdata[len(chunk):] = 0
            cursor = len(audio)
            raise sd.CallbackStop
        outdata[:] = chunk.reshape(-1, 1)
        cursor = end
        rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
        service._latest_amplitude = min(rms / settings.tts_calibration_max, 1.0)
    return _cb
```

When the stream closes (CallbackStop), set `_latest_amplitude = 0.0` so the orb settles after TTS ends.

**Cancellation handling:** the existing `cancel_playback()` (added Day 11 for mute-during-speaking) calls `sd.stop()`. After cancellation, also force `_latest_amplitude = 0.0` so the orb doesn't ghost.

### T-3. Backend: amplitude broadcast loop

**File:** `backend/services/conversation.py` (the orchestrator already owns the lifecycle of recording and speaking)

A single background asyncio task started during the LISTENING and SPEAKING stages, stopped when leaving them:

```python
# Polls latest_amplitude from whichever source is active, broadcasts via WebSocket.
# 20Hz cadence matches the audio callback rate, so no upstream throttling needed.
async def _broadcast_amplitude(self, source: str):
    """source: 'mic' or 'tts'. Reads from self._recorder or self._tts."""
    interval = 1.0 / settings.amplitude_broadcast_hz
    while True:
        if source == "mic":
            value = self._recorder.latest_amplitude
        else:
            value = self._tts.latest_amplitude
        await ws_manager.broadcast({"type": "amplitude", "value": value, "source": source})
        await asyncio.sleep(interval)
```

Start at the LISTENING transition with `source="mic"`; start at the SPEAKING transition with `source="tts"`; cancel the task on the next state transition. Store the task in `self._amp_broadcast_task` and cancel it on every state change to make sure overlapping tasks never happen.

**Gotcha to handle:** if the user mutes mid-speaking, the SPEAKING-exit handler must cancel the amplitude task in addition to cancelling TTS playback. Per the voice-pipeline skill, both go in the "side effects after the lock" section of `on_mute_toggle`.

### T-4. Frontend: extend `VoiceEvent` union

**File:** `frontend/src/hooks/useWebSocket.ts`

Add the new event type to the union:

```ts
// Day 16 — high-frequency amplitude events (NOT queued; see hook implementation)
| { type: "amplitude"; value: number; source: "mic" | "tts" }
```

Note in the comment that this event type is intentionally excluded from the reducer queue — readers searching for "amplitude" should immediately understand why it's not handled in the dispatch effect in `App.tsx`.

### T-5. Frontend: amplitude side channel in `useVoiceEvents`

**File:** `frontend/src/hooks/useWebSocket.ts`

Two changes:

**1.** Add an `amplitudeRef` returned alongside `events` and `dispatch`:

```ts
export interface VoiceEventsHook {
  events: VoiceEvent[];
  dispatch: Dispatch<QueueAction>;
  amplitudeRef: React.MutableRefObject<number>;  // NEW — read by Blob's rAF loop
}
```

**2.** In the WebSocket `onmessage` handler, route `amplitude` events directly to the ref, never dispatch:

```ts
ws.onmessage = (e) => {
  try {
    const ev = JSON.parse(e.data) as VoiceEvent;
    if (ev.type === "amplitude") {
      // Side channel: latest value wins. Never goes through the reducer queue.
      // Reason: high-frequency (~20Hz), order doesn't matter, losing one is invisible.
      amplitudeRef.current = ev.value;
      return;
    }
    console.log("voice event:", ev);
    dispatch({ type: "event_received", event: ev });
  } catch (err) {
    console.error("WS: bad payload:", e.data, err);
  }
};
```

The `console.log` should also be skipped for amplitude — at 20Hz, the devtools console becomes useless otherwise.

**Initialize the ref to 0.0** and reset to 0 on disconnect (in `ws.onclose`, just before `dispatch({ type: "clear" })`).

### T-6. Frontend: wire amplitude to `App.tsx` and `Blob.tsx`

**Files:** `frontend/src/App.tsx`, `frontend/src/blob/Blob.tsx`

**App.tsx:**

```ts
const { events, dispatch, amplitudeRef } = useVoiceEvents();
// ...
<Blob voiceState={voiceState} size={180} amplitudeRef={amplitudeRef} />
```

Also add the **amplitude reset on state transitions away from listening/speaking** — in the dispatch effect's `state_changed` branch:

```ts
} else if (event.type === "state_changed") {
  setVoiceState(event.state);
  // Day 16: clear amplitude when leaving an audio-active state so the orb
  // doesn't briefly ghost into thinking/idle with stale reactivity.
  const audioActive = event.state === "listening" || event.state === "speaking";
  if (!audioActive) amplitudeRef.current = 0;
}
```

This is small but important — without it, the orb keeps reacting to whatever the last amplitude value was for ~200ms after the state change. Subtle but visible.

**Blob.tsx:** accept `amplitudeRef` as a prop with `React.MutableRefObject<number>` type. Add a `smoothedAmpRef` (internal `useRef(0)`) and apply EMA inside the rAF loop.

### T-7. `BlobStates.ts`: add reactivity coefficients

**File:** `frontend/src/blob/BlobStates.ts`

Extend `BlobStateConfig` with an `audio` sub-object:

```ts
interface BlobStateConfig {
  // ... existing fields ...

  /**
   * Audio reactivity coefficients. Each is multiplied by the smoothed amplitude
   * (range 0.0–1.0) and added to the corresponding base value. Zero means this
   * state ignores amplitude on that channel.
   *
   * Only `listening` and `speaking` have non-zero values — other states have no
   * audio source active, so reacting would invent a signal that doesn't exist.
   */
  audio: {
    pulseK: number;            // scale: 1 + amp * pulseK (speaking only)
    conicSpeedK: number;       // conic rotation: baseSpeed * (1 + amp * conicSpeedK)
    colorAlphaK: number;       // radial gradient alpha: baseAlpha * (1 + amp * colorAlphaK)
    conicSaturateK: number;    // conic saturate(): 1 + amp * conicSaturateK
    highlightSizeK: number;    // spec size: baseSize * (1 + amp * highlightSizeK)
    highlightAlphaK: number;   // spec alpha: 0.45 + amp * highlightAlphaK
  };
}
```

Fill in per the spec table at the top of this document. Most states get `{ pulseK: 0, conicSpeedK: 0, colorAlphaK: 0, conicSaturateK: 0, highlightSizeK: 0, highlightAlphaK: 0 }`. Only listening and speaking get the real values.

For readability, consider defining a `NO_AUDIO_REACTIVITY` const at the top of the file and spreading it into the four states that don't react. Saves repetition and makes the *intentionally zero* states scannable.

### T-8. `Blob.tsx`: amplitude integration in the rAF loop

**File:** `frontend/src/blob/Blob.tsx`

Add the smoothed amplitude reading and per-frame application:

```ts
// EMA smoothing of raw amplitude. Asymmetric: faster attack (k=0.35) so the orb
// feels responsive to sudden sounds; slower decay (k=0.12) so the orb settles
// over ~half a second rather than strobing on each syllable gap.
const smoothedAmpRef = useRef(0);

// ... inside rAF loop ...
const rawAmp = amplitudeRef.current;
const k = rawAmp > smoothedAmpRef.current ? 0.35 : 0.12;
smoothedAmpRef.current += (rawAmp - smoothedAmpRef.current) * k;
const amp = smoothedAmpRef.current;

// Then apply per the current state's coefficients
const audio = configRef.current.audio;
const pulseScale = 1 + amp * audio.pulseK;
const conicSpeedMult = 1 + amp * audio.conicSpeedK;
const radialAlphaMult = 1 + amp * audio.colorAlphaK;
const conicSat = 1 + amp * audio.conicSaturateK;
const specSizeMult = 1 + amp * audio.highlightSizeK;
const specAlpha = 0.45 + amp * audio.highlightAlphaK;
// ... use these in the existing morph/scale/conic/radial/highlight calculations ...
```

The math hooks into the existing per-frame calculations from Day 15. The `motion-value-driven` Framer Motion interpolation between states is untouched — amplitude is a *frame-local* multiplier on top of whatever the motion values currently are.

### T-9. Dev cycler enhancement (optional, 10 min)

**File:** `frontend/src/App.tsx`

The Day 15 dev cycler force-sets `voiceState` but provides no way to test amplitude reactivity without actually triggering the voice loop. Quick addition: a slider next to the cycler in dev mode that writes a value into `amplitudeRef.current`, simulating amplitude. Lets you cycle to `listening`, drag the slider, see the orb react.

Optional because manual testing through the real voice loop also works — but a few minutes here pays back over the rest of the day.

Both the cycler and slider get removed on Day 17 polish.

---

## What's deliberately out of scope today

- **Edge deformation.** Replaced by color bloom + conic + pulse per Day 15 design conversation. The Day-by-Day plan calls for edge deform; we're consciously not implementing it.
- **Per-user calibration UI.** Mic calibration max is hardcoded in settings; tuning happens by editing the YAML/env, not a UI slider. Day 17 polish material.
- **Visualization of amplitude in the chat panel** (e.g. a small VU meter). The orb itself is the visualization.
- **Long-term amplitude averaging.** No "average loudness" feedback for the user. Out of scope.
- **Logging amplitude broadcasts.** Don't log every amplitude event — would flood `data/logs/jarvis.log` at 20Hz × turn-length seconds. Log only the first and last value per turn for debugging.

If you find yourself adding any of these "for completeness," stop and write the idea down in `docs/journal.md` instead.

---

## Manual test checklist (run at end of day)

1. **CPU baseline.** After PF-1, idle CPU on WebView under 10%. Record value in journal.
2. **Mic reactivity, normal volume.** Hold Alt+Space, speak at normal conversational volume. Orb shows color bloom + slow conic acceleration. No pulse (correct — listening doesn't pulse). Releasing PTT → orb returns to thinking palette within ~500ms (smoothing decay).
3. **Mic reactivity, loud and quiet.** Test both extremes. Whispering produces a faint glow; shouting saturates colors. No clipping artifacts; no visible jitter on either end.
4. **TTS reactivity.** Ask a question that returns a multi-sentence response. Orb pulses with syllables, conic visibly accelerates, colors brighten. Pulse should be subtle (5% scale max) — if it feels like a heartbeat, drop pulseK to 0.04.
5. **State-transition reset.** Watch carefully as listening → thinking transitions. The color bloom should fade out smoothly via the 600ms Framer Motion state transition + the amplitude reset to 0. No ghost reactivity bleeding into thinking.
6. **Mute mid-speaking.** Start a turn, mute mid-TTS (Ctrl+Alt+J). TTS stops, orb transitions to muted state cleanly, no leftover amplitude pulsing. Re-unmute, hit PTT again. Full cycle works.
7. **Thinking remains calm.** During the thinking phase of a turn (after STT, before TTS), confirm the orb shows no reactivity — palette deepens, motion slows, no color bloom. This is the spec.
8. **Network/error path.** Force an STT or LLM failure (briefly unset an API key). Orb transitions to error state cleanly with no amplitude artifacts. Auto-recovers to idle after 3s.
9. **20 minutes of normal use.** Use it for actual work for 20 minutes — log a project note, ask a question, etc. Note any feel-bad moments. Add them to journal.

If items 1–8 pass, you're done. Item 9 is the real test — does the reactivity make the orb feel *more alive* during use, or does it become a distraction?

---

## Completion criteria

- [ ] Idle CPU on WebView under 10% (record actual in journal)
- [ ] `backend/voice/audio.py` exposes `latest_amplitude` updated per callback chunk
- [ ] `backend/voice/tts.py` refactored to OutputStream callback; `latest_amplitude` updates during playback; resets to 0 on cancellation
- [ ] `backend/services/conversation.py` runs `_broadcast_amplitude` background task during listening and speaking states; cancels on state change
- [ ] `VoiceEvent` union extended with `amplitude` event type
- [ ] `useVoiceEvents` routes `amplitude` events to a side-channel `amplitudeRef` instead of the queue
- [ ] `App.tsx` passes `amplitudeRef` to `Blob` and clears it to 0 on transitions out of listening/speaking
- [ ] `BlobStates.ts` extended with `audio` coefficients per state per the spec table
- [ ] `Blob.tsx` reads `amplitudeRef`, applies EMA smoothing (0.35 attack, 0.12 decay), uses smoothed value to modulate visuals
- [ ] All 9 manual tests pass
- [ ] You can explain the queue-vs-side-channel architectural decision aloud without looking at the code

---

## Watch-outs / gotchas

- **TTS calibration_max may need tuning.** Piper output level depends on the voice model. `en_GB-alan-medium` (current voice) may differ from `en_US-lessac-medium`. If TTS amplitude looks pinned at 1.0 (constant maximum) or stays near 0, adjust `tts_calibration_max` in settings. Quick diagnostic: log `latest_amplitude` once per second during a known TTS playback; should range roughly 0.2–0.8.
- **sounddevice OutputStream cleanup matters.** The stream must be closed properly to release the audio device. Use `with sd.OutputStream(...) as stream:` or explicitly close in a try/finally. A leaked stream causes "audio device busy" errors on the next TTS call.
- **CallbackStop inside the OutputStream callback** is the correct way to signal end-of-stream from inside a callback. Don't return; raise the exception. sounddevice catches it and shuts down cleanly.
- **Amplitude broadcasts must not log at INFO level.** 20Hz × 60-second listening = 1200 log lines per minute. Log at DEBUG and gate behind a setting, or skip logging entirely for amplitude.
- **Don't await inside `_broadcast_amplitude`'s loop with long sleeps.** `asyncio.sleep(0.05)` is fine; longer sleeps would delay cancellation responsiveness. The whole task should cancel within one tick when its state change arrives.
- **The mic and TTS amplitude refs on the backend share the same broadcast event type.** The `source` field distinguishes them. The frontend doesn't currently care which is which (uses the same coefficients per active state), but having the field there means we can later show different VU meters or apply different smoothing per source without a protocol change.
- **EMA smoothing is the difference between "lifelike" and "robotic."** If the orb feels twitchy after T-8, the attack k is too high (try 0.25). If it feels laggy, the decay k is too high (try 0.08). Tune these in `Blob.tsx`, not in `BlobStates.ts` — they're not per-state.
- **`amplitudeRef.current = 0` on disconnect** in `useVoiceEvents` is important. Without it, the orb keeps reacting to the last value forever after a WebSocket drop.
- **Don't reach into Framer Motion's interpolating motion values for amplitude math.** The motion values are interpolating between state configs (the 600ms state-change lerp). Amplitude is a frame-local multiplier on top of whatever those values currently are. Read `motionValue.get()` for the base, then multiply by `(1 + amp * K)`. Keep them as separate concerns.

---

## Git commit messages

Three logical commits work best — backend amplitude, frontend plumbing, blob integration. Or fold into one if the day stays small.

```
feat(voice): per-chunk amplitude calculation and broadcast

- AudioRecorder exposes latest_amplitude (RMS, normalized 0-1)
- TTSService refactored to OutputStream callback for per-chunk amplitude
- ConversationOrchestrator broadcasts amplitude at 20Hz during
  listening + speaking states; cancels broadcast task on state change
- Mic/TTS calibration max in settings (tunable per environment)
```

```
feat(frontend): amplitude side channel in voice events hook

- VoiceEvent union extended with { amplitude, value, source }
- useVoiceEvents returns amplitudeRef alongside events queue
- amplitude events bypass the reducer queue; latest value wins
- Resets to 0 on disconnect and on state-change out of listening/speaking
```

```
feat(blob): audio reactivity per Day 15 design spec

- BlobStateConfig.audio: per-state reactivity coefficients
- Only listening + speaking react; idle/thinking/muted/error stay at zero
- Blob.tsx applies EMA-smoothed amplitude per frame (0.35 attack, 0.12 decay)
- Reactivity: color bloom + conic speed + (speaking only) pulse
- Listening uses calmer conic boost (0.6) than speaking (2.8) — "thinking
  while listening" vs "actively speaking"

Also: batch style writes into cssText per frame (Day 15 CPU carryover).
```

---

## Time budget

| Phase | Estimate |
|---|---|
| PF-1 CPU optimization | 20 min |
| PF-2 Palette tuning pass | 25 min |
| T-1 Mic amplitude | 30 min |
| T-2 TTS OutputStream refactor | 1.5 hours (the hardest backend bit) |
| T-3 Broadcast loop in orchestrator | 45 min |
| T-4 + T-5 Frontend type + side channel | 30 min |
| T-6 App.tsx wiring + state reset | 20 min |
| T-7 BlobStates.ts coefficients | 15 min |
| T-8 Blob.tsx rAF integration | 1 hour |
| T-9 Dev cycler amplitude slider (optional) | 10 min |
| Manual testing | 30 min |
| Buffer / unforeseen | 45 min |
| **Total** | **~6.5–7 hours** |

The original plan budgeted 4 hours for Day 16 — but Day 15 used 5.5–6 of its 6-hour budget, so we're calibrated to "real" estimates now. Day 16 is heavier than the original plan because the backend has two amplitude sources (mic *and* TTS) instead of just one, and TTS amplitude requires the OutputStream callback refactor.

If the day goes over, the absorbing buffer is Days 18–19 of Week 3 (already designated as buffer in the Day-by-Day plan).

---

## Workflow reminders

- **Write the signature first** for the new methods — `_broadcast_amplitude`, `_make_callback`, the augmented `VoiceEventsHook`. Write the type/signature, then ask for the implementation. Read every line.
- **Test backend separately from frontend.** After T-1/T-2/T-3, you should be able to see `amplitude` events flowing in the WebSocket inspector (browser devtools → Network → WS) before any frontend changes. Verify the protocol works in isolation before plumbing it through React.
- **Commit per task, not per day.** Three small commits per the message templates above is good.
- **Don't tune everything at once.** When the orb feels slightly off, change *one* coefficient, observe, then the next. Otherwise you'll lose track of which knob did what.

---

## When you get stuck

The likely stuck points and what to do:

- **TTS playback breaks after the refactor:** start by verifying the OutputStream produces audio at all (try with the amplitude calc commented out). If audio works but no amplitude updates, the callback is probably catching its own exceptions silently — add `print()` statements inside the callback (they show up despite the threading model).
- **Amplitude looks correct on backend (logs show non-zero values) but blob doesn't react:** check the side-channel routing in `useVoiceEvents.onmessage`. Most likely cause is the amplitude branch falling through to `dispatch` and getting eaten by the reducer's unknown-type case.
- **Orb feels twitchy / strobing:** smoothing isn't aggressive enough. Drop the attack k to 0.25 and/or raise decay k to 0.08. Re-test after each change.
- **Orb feels laggy / sluggish:** opposite — smoothing is too aggressive. Raise attack k to 0.45, lower decay k to 0.15.
- **TTS amplitude pinned at 1.0:** `tts_calibration_max` is too low. Try doubling it. Find the value where loud syllables hit ~0.8 and quiet phonemes hit ~0.2.
- **TTS amplitude near zero throughout:** `tts_calibration_max` is too high, or the OutputStream isn't actually receiving samples. Log a sample chunk's mean absolute value to confirm audio is flowing.
- **Conic stripe becomes visible at high amplitude during speaking:** conic blur is dropping below threshold. Either keep `conicBlur` ≥ 24px or scale blur *up* slightly with amplitude (subtle, opposite direction) to hide stripes during peaks.

If you're stuck >30 min on any one thing, paste the symptom + relevant code snippet and ask. Don't grind.
