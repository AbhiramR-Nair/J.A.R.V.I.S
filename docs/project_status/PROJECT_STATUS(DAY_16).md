# Project Status — Day 16

**Period covered:** Day 16 (Week 3, Day 2 — Audio Reactivity)
**Status:** Complete — all mandatory tasks done, T-9 (optional dev slider) also completed.
**Environment:** Windows 11, Python 3.13.5, FastAPI, React 19 + Vite, Framer Motion 12.40, Groq Whisper-large-v3, Piper `en_GB-alan-medium`, Gemini 2.5 Flash

> Checkpoint summary for Day 16: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 17.

---

## 1. What has been done

Day 16 gave the orb hearing. Mic input and TTS output amplitude now drive subtle continuous
motion in the blob — the conic spins faster, radial colours bloom, the specular highlight
grows, and (during speaking only) the orb pulses slightly with the assistant's voice.

| Task | What landed | Status |
|---|---|---|
| PF-1 — CPU optimization | Batched `style.*` writes into `element.style.cssText` on `wrapperRef` and `conicRef`. Removed conflicting JSX `style` props from those two elements (rAF now owns their inline styles entirely). Idle CPU: ~13% → ~6% | Done |
| PF-2 — Palette tuning | Visual review of all 6 states after overnight rest. No changes needed — palettes passed review. | Done (no changes) |
| T-1 — Mic amplitude | `AudioRecorder._on_audio` callback now computes per-chunk RMS. int16 normalized to `[-1, 1]` float before RMS so `mic_calibration_max` lives in the same `[0, 1]` domain. `latest_amplitude` property exposed. Three new settings: `mic_calibration_max=0.1`, `tts_calibration_max=0.3`, `amplitude_broadcast_hz=20` | Done |
| T-2 — TTS amplitude | `TTSService` refactored from `sd.play()` + `sd.wait()` to an `sd.OutputStream` with a pull callback (`_make_callback`). Callback fills audio chunks, computes RMS, writes `service._latest_amplitude`. `_active_stream` stored so `cancel_playback()` can abort it. `finally` block guarantees cleanup. | Done |
| T-3 — Broadcast loop | `_broadcast_amplitude(source)` task added to `ConversationOrchestrator`. Started by `_transition()` on entry to LISTENING and SPEAKING; cancelled by `_transition()` on every state change. 20Hz cadence. No logging inside the loop (20Hz × long turns = thousands of lines). | Done |
| T-4 — VoiceEvent union | `{ type: "amplitude"; value: number; source: "mic" \| "tts" }` added to the union with comment explaining it bypasses the queue. | Done |
| T-5 — Amplitude side channel | `useVoiceEvents` returns `amplitudeRef` alongside `events` and `dispatch`. In `onmessage`, amplitude events write directly to `amplitudeRef.current` and return early — never dispatched to the reducer queue. Reset to 0 on disconnect. | Done |
| T-6 — App.tsx wiring | `amplitudeRef` destructured from hook, passed to `<Blob>`. Amplitude reset to 0 in the `state_changed` branch when transitioning away from listening/speaking. | Done |
| T-7 — BlobStates coefficients | `BlobStateConfig.audio` sub-object added: 6 coefficients per state. `NO_AUDIO_REACTIVITY` const spread into 4 silent states. Listening and speaking have real values per the spec table. | Done |
| T-8 — rAF amplitude integration | `smoothedAmpRef` added. EMA computed each frame (0.35 attack / 0.12 decay). 6 multipliers derived from smoothed amplitude and applied to: conic rotation speed, wrapper scale (speaking only), conic `saturate()` filter, radial `brightness()` filter, specular size, specular alpha. | Done |
| T-9 — Dev amplitude slider | Range input inside the `import.meta.env.DEV` block writes to `amplitudeRef.current` directly. `devAmp` state mirrors it for display. Allows testing reactivity without triggering the voice loop. Removed Day 17. | Done (optional) |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. cssText batching — why it required removing JSX `style` props

The naive approach to cssText batching (`element.style.cssText = "property: value"`) replaces
the *entire* inline style attribute, wiping any properties React had written via the `style`
prop. For `wrapperRef` and `conicRef`, the fix was to remove those elements' `style` props
from JSX entirely, making the rAF loop the sole owner of their inline styles. Static layout
properties (position, overflow, inset, border-radius, pointer-events) were folded into the
cssText string alongside the dynamic ones. `size` (180px, hardcoded at the call site) is
captured in the rAF closure at mount — safe since the prop never changes in practice.

The other three elements (`radialRef`, `highlightRef`, `grainRef`) were left with individual
property writes because they already had only one or two dynamic writes, and their JSX
`style` props contain properties (like `mix-blend-mode: screen`) that would complicate cssText
management. Not worth the churn for single-write elements.

### 2. Amplitude as a side channel, not a queue event

The event reducer queue (`useVoiceEvents`) was designed to guarantee every state event fires
exactly once in order. Amplitude is the opposite kind of signal: ~20Hz, latest value wins,
missing one frame is invisible. Mixing amplitude into the queue would let a backlog of
amplitude events delay a `speaking_ended` state change by several seconds. So amplitude
bypasses the queue entirely: written to a `useRef` directly in `onmessage`, read each rAF
frame. This is the same GIL-atomic float pattern used on the backend.

### 3. TTS OutputStream refactor — why `sd.play()` had to go

`sd.play()` uses sounddevice's internal "default stream." `sd.stop()` stops that same
default stream, which is why `cancel_playback()` worked before. When switching to an explicit
`sd.OutputStream`, `sd.stop()` has no effect on it — a different stream object entirely.
The solution: store the active stream as `self._active_stream` on `TTSService` so
`cancel_playback()` can call `stream.abort()` directly. The `finally` block in
`_play_with_amplitude` guarantees `_active_stream = None` and `_latest_amplitude = 0.0`
regardless of how the stream exits (completion or abort).

### 4. int16 normalization before RMS

The plan's code example computed RMS directly on the int16 array and used
`mic_calibration_max = 0.3` as the normalisation target. For raw int16, RMS of a typical
speaking voice is in the hundreds-to-thousands range (on a 0–32768 scale) — a calibration
max of 0.3 would make the orb react only to imperceptibly quiet sounds. The fix: normalize
to `[-1.0, 1.0]` float first (divide by 32768), then compute RMS. In that domain, normal
laptop mic speech has RMS around 0.05–0.12, so `mic_calibration_max = 0.1` saturates at a
loud-but-normal level. Same normalization applied to TTS PCM.

### 5. EMA smoothing — asymmetric attack/decay

Symmetric EMA (same k for rise and fall) either feels laggy (low k) or strobes on syllable
gaps (high k). The asymmetric approach — faster attack (k=0.35) so the orb feels responsive,
slower decay (k=0.12) so the orb settles gracefully over ~500ms — is the standard trick for
audio-reactive visuals. The smoothing runs at 60fps in the rAF loop, decoupled from the 20Hz
amplitude broadcast rate; the EMA naturally interpolates between broadcast ticks.

### 6. Amplitude reset on state transition in App.tsx

Without the explicit `amplitudeRef.current = 0` on state transitions away from
listening/speaking, the EMA would decay from the last amplitude value rather than from zero.
At decay k=0.12, a value of 0.8 takes roughly 30 frames (~500ms) to reach near-zero.
During that window the orb would show subtle reactivity during thinking/idle — wrong
semantics ("colour bloom = I hear sound" would be broken). The reset forces the EMA to
start decaying from zero immediately after the transition.

---

## 3. Problems faced and how they were handled

### Problem 1 — `MutableRefObject` deprecated in React 19

**What happened:** The plan specified `React.MutableRefObject<number>` as the type for
`amplitudeRef` in the `VoiceEventsHook` interface. React 19 (this project's version) marks
`MutableRefObject` as deprecated in favour of `RefObject`. However, `RefObject.current` is
typed as `readonly` in React 19, which would prevent the amplitude ref from being written to.

**Fix:** Used `{ current: number }` as the type throughout — in `VoiceEventsHook`,
`BlobProps`, and anywhere `amplitudeRef` is annotated. This is a structural type that
describes exactly what the consumers need (a mutable object with a numeric `current`
property) without depending on deprecated React internals.

**Rule going forward:** When annotating mutable refs passed as props or returned from hooks
in React 19, use `{ current: T }` instead of `MutableRefObject<T>`.

### Problem 2 — cssText wipes React-managed inline styles

**What happened:** First attempt at cssText batching on `wrapperRef` caused the element to
lose its `width`, `height`, `position`, `overflow`, and `WebkitAppRegion` styles (which
React had written via the JSX `style` prop), making the wrapper collapse to 0×0.

**Fix:** Removed the JSX `style` prop from `wrapperRef` and `conicRef` entirely and folded
all their properties (both static layout and dynamic rAF-driven) into the cssText string.
The rAF loop is now the sole writer of inline styles on those elements. Static layout
properties for the other three elements were left in JSX `style` props unchanged.

### Problem 3 — `sd.stop()` doesn't stop an explicit `OutputStream`

**What happened:** After refactoring TTS playback to use `sd.OutputStream`, `cancel_playback()`
still called `sd.stop()` (the old approach). `sd.stop()` only stops sounddevice's default
stream — it has no effect on an explicitly created `OutputStream`. Muting mid-speaking would
call `cancel_playback()` but TTS would keep playing.

**Fix:** Added `self._active_stream: sd.OutputStream | None = None` to `TTSService`. The
`_play_with_amplitude` function sets it before the polling loop and clears it in the `finally`
block. `cancel_playback()` now reads `self._active_stream` and calls `stream.abort()` if
active. `abort()` discards buffered audio immediately (unlike `stop()` which waits for the
buffer to drain), which is the correct behaviour for a user mute action.

---

## 4. Heads-up: downstream complications to watch

### CPU peaks ~15% during state transitions

PF-1 reduced idle CPU from ~13% to ~6%, but peaks of ~15% remain during state transitions.
These coincide with Framer Motion simultaneously animating all 14 motion values over 600ms.
Not a regression from Day 15 — these peaks existed before. They are transient (600ms) and
only occur when the voice state changes, not during steady animation.

**Mitigation if needed:** Reduce the number of animated motion values (e.g., consolidate
the 6 conic stops into fewer by reusing colours), or increase the transition duration to
spread the animation work over more frames. Not worth addressing in v1.

### TTS calibration max likely needs tuning

`tts_calibration_max = 0.3` is a starting default. Piper's output level varies by voice
model — `en_GB-alan-medium` may differ from what the default was designed for. If TTS
amplitude looks pinned at 1.0 throughout a response (orb constantly at max brightness) or
stays near 0 (no visible reaction), adjust this value in `.env` or `settings.py`.

**Diagnostic:** Temporarily log `self._tts.latest_amplitude` once per second during a known
TTS response. Target range: 0.2–0.8 for normal speech, with quiet phonemes ~0.2 and loud
syllables ~0.8.

### Dev cycler and amplitude slider must be removed on Day 17

Both the state cycler buttons and the amplitude slider are guarded by `import.meta.env.DEV`
and invisible in production. However, the `devAmp` state variable and the JSX blocks remain
in source until Day 17.

**Mitigation:** Search for `import.meta.env.DEV` in `App.tsx` on Day 17 — there is exactly
one block, containing both the state cycler and the amplitude slider. Delete the entire block
and the `devAmp` / `setDevAmp` state declaration.

### `_active_stream` race window on cancel

There is a theoretical race: `_play_with_amplitude` sets `service._active_stream = stream`
*inside* the `with sd.OutputStream(...) as stream:` block, after the stream opens. If
`cancel_playback()` is called in the milliseconds between when the executor starts
`_play_with_amplitude` and when `_active_stream` is set, `cancel_playback()` sees
`_active_stream = None` and does nothing — TTS would play through.

**In practice:** The executor call (`run_in_executor`) and the `with ... as stream:` block
execute synchronously in the thread. `cancel_playback()` is called from the asyncio event
loop (single-threaded). By the time any asyncio code runs `cancel_playback()`, the executor
thread has already opened the stream and set `_active_stream`. Not a real risk in v1 — the
mute action requires user input which takes at least several hundred milliseconds.

---

## 5. How to verify Day 16

```powershell
# 1. CPU baseline in idle
# Start both terminals, let the app settle for 30s.
# Check Task Manager → msedgewebview2.exe → should be 5-8% idle
# Peaks to ~15% for 600ms during state transitions — expected.

# 2. Mic reactivity (via dev cycler)
# Cycle to 'listening'. Drag amplitude slider from 0 to 1.
# Expected: conic spins visibly faster, radial colours brighten.
# Expected: no scale pulse (pulseK=0 in listening).
# Expected: drag back to 0, orb returns to base over ~500ms (EMA decay).

# 3. TTS reactivity (via dev cycler)
# Cycle to 'speaking'. Drag amplitude slider from 0 to 1.
# Expected: conic spins much faster (conicSpeedK=2.8 vs 0.6 in listening).
# Expected: subtle scale bounce (pulseK=0.05).

# 4. Real voice loop — mic reactivity
# Hold Alt+Space, speak at normal volume.
# Expected: colour bloom + conic acceleration visible while speaking.
# Expected: releasing PTT → orb transitions to thinking, amplitude drops cleanly.

# 5. Real voice loop — TTS reactivity
# Ask a multi-sentence question.
# Expected: orb pulses and conic accelerates during TTS playback.
# Expected: after TTS ends → orb transitions to idle, no ghost reactivity.

# 6. Mute mid-speaking
# Start a turn, mute with Ctrl+Alt+J mid-TTS.
# Expected: TTS stops immediately, orb transitions to muted, no amplitude ghosting.

# 7. Thinking state is calm
# During thinking phase, confirm no colour bloom — palette deepens, motion slows.
```

All checks passed on 2026-05-28.

---

## 6. Open items before Day 17

- [ ] Remove dev cycler + amplitude slider — search `import.meta.env.DEV` in `App.tsx`
- [ ] Tune `tts_calibration_max` if TTS reactivity feels wrong (too much or too little)
- [ ] Day 17 work: drag-to-move, snap-to-corner, settings panel (mic device, project switcher),
      chat history (last 5 messages). Tag `v0.3.0-blob` at end of Day 19.

---

## 7. Files changed this day

```
EDIT:
  backend/config/settings.py          — mic_calibration_max, tts_calibration_max,
                                        amplitude_broadcast_hz settings added
  backend/voice/audio.py              — _latest_amplitude field, RMS in callback,
                                        latest_amplitude property, reset on stop
  backend/voice/tts.py                — _make_callback + _play_with_amplitude replace
                                        _play_sync; _active_stream, _latest_amplitude,
                                        _tts_calibration_max on service; cancel_playback
                                        uses stream.abort() instead of sd.stop()
  backend/services/conversation.py    — _amp_broadcast_task field, _broadcast_amplitude
                                        method, _transition() starts/cancels task,
                                        close() cancels on shutdown
  frontend/src/hooks/useWebSocket.ts  — amplitude event type in VoiceEvent union;
                                        amplitudeRef side channel in useVoiceEvents;
                                        amplitude reset on disconnect
  frontend/src/App.tsx                — amplitudeRef destructured + passed to Blob;
                                        amplitude reset on state_changed; devAmp state
                                        + amplitude slider in DEV block
  frontend/src/blob/BlobStates.ts     — BlobStateConfig.audio sub-object; NO_AUDIO_REACTIVITY
                                        const; per-state coefficients
  frontend/src/blob/Blob.tsx          — amplitudeRef prop; smoothedAmpRef; EMA in rAF loop;
                                        6 amplitude multipliers applied to conic speed,
                                        wrapper scale, conic saturate, radial brightness,
                                        specular size + alpha; cssText batching on
                                        wrapperRef and conicRef (PF-1)
```

---

## 8. Commits

```
(pending)
```
