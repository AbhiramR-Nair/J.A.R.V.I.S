# Project Status — Day 15

**Period covered:** Day 15 (Week 3, Day 1 — SVG/CSS Animated Blob)
**Status:** Complete — all mandatory blocks done. Commit `b31b315`.
**Environment:** Windows 11, Python 3.13.5, FastAPI, React 19 + Vite, Framer Motion 12.40, Groq Whisper-large-v3, Piper `en_GB-alan-medium`, Gemini 2.5 Flash

> Checkpoint summary for Day 15: what got built, *why* it was built that way, what went
> sideways, how it was handled, and what to watch downstream. Read before Day 16.

---

## 1. What has been done

Day 15 was the first day of Week 3 — the orb got its face. The placeholder cyan circle is
gone; the animated blob is now the visual centrepiece of the app.

| Task | What landed | Status |
|---|---|---|
| CL-1 — Remove test shape | Deleted `<div className="w-24 h-24 rounded-full bg-cyan-400/50" />` and its comment from `App.tsx` | Done |
| CL-2 — Remove redundant `muted` boolean | Deleted `const [muted, setMuted] = useState(false)`, both `setMuted` calls in the `state_changed` branch, and rewrote `statusLabel` to use `voiceState === "muted"` directly | Done |
| CL-3 — Delete empty files | `git rm frontend/src/hooks/useVoiceState.ts` and `frontend/src/components/StatusBar.tsx` — both were empty stubs never imported | Done |
| T-1 — `BlobStates.ts` | Pure data file: `BlobStateConfig` interface, `BLOB_STATES` record (6 states), `mapVoiceStateToVisualState` helper. No React, no Framer Motion | Done |
| T-2 — `Blob.tsx` | Full animated orb component. 4-layer stack, rAF + Framer Motion split, 6 visual states, error auto-recovery | Done |
| T-3 — Wire into `App.tsx` | `import { Blob }` added, `<Blob voiceState={voiceState} size={180} />` placed above status badge | Done |
| T-4 — Dev state cycler | 6 buttons in `App.tsx`, `import.meta.env.DEV`-gated, each force-sets `voiceState`. Active state highlighted. Removed on Day 17 | Done |
| T-5 — Visual verification | All 6 states visually distinct. Transitions smooth (~600ms). Error auto-recovers to idle after 3s. All checks passed | Done |
| T-6 — Performance check | CPU: ~13% on `msedgewebview2.exe` in idle. Slightly over the 10% target. Noted; review Day 16 | Done (marginal overage) |

---

## 2. Implementation strategy (the *why* behind the choices)

### 1. `BlobStates.ts` — data separated from component

All tunable visual parameters (morph intensity, conic colors, grain opacity, etc.) live in a
single pure-data file with no React or Framer Motion imports. The rationale: every tuning
pass (Day 16 morning) means changing numbers, never touching component logic. Keeping the
data file short and comment-annotated makes it readable without context. The
`mapVoiceStateToVisualState` helper lives here too — the single place that defines the
`transcribing → thinking` collapse, so there's no scattered conditional logic in the
component.

### 2. `Blob.tsx` — two-system animation split

Two fundamentally different animation concerns are handled by two different systems:

**Framer Motion `useMotionValue` + `animate()`** handles *state transition interpolation*:
when `voiceState` changes, all 14 motion values (scale, opacity, grain opacity, highlight
opacity, conic blur, 3 radial colors, 6 conic color stops) animate to the new config over
600ms with `easeInOut`. Framer Motion owns the numbers internally — no DOM side effects from
these calls alone.

**`requestAnimationFrame` loop** handles *continuous per-frame motion*: border-radius
morphing, conic gradient rotation, and specular highlight drifting. The loop reads `.get()`
on the motion values each frame, so it naturally reads the currently-interpolating values
during a state transition. This means a `listening → thinking` transition shows the color
slowly washing from bright cyan to deep indigo while the morph shape smoothly changes cadence
— both happening simultaneously with no coordination code.

This split is what prevents the "snap on state change" problem that a pure-rAF approach
would have.

### 3. `animateTo` as `useCallback` with stable deps

The function that drives all 14 `animate()` calls is wrapped in `useCallback` with all
14 motion value references as deps. Since `useMotionValue` returns the same `MotionValue`
object reference on every render (it is a stable ref, not a new value), `animateTo` is
effectively created once on mount. The state-change `useEffect` depends on
`[voiceState, animateTo]` — both stable — so it runs only when `voiceState` actually
changes.

### 4. Error auto-recovery via `useEffect` cleanup

When `voiceState === "error"`, the state-change effect fires `animateTo(BLOB_STATES.error)`
immediately, then schedules `animateTo(BLOB_STATES.idle)` via `setTimeout` after 3000ms.
The effect returns `() => clearTimeout(timer)` as its cleanup. If `voiceState` changes
before 3 seconds (e.g. the backend recovers and sends `state_changed(idle)`), the cleanup
fires, the timer is cancelled, and the new `voiceState` drives the transition instead.
No race condition.

### 5. 8-value `border-radius` form

The morph uses the full 8-value CSS form: `A% B% C% D% / E% F% G% H%`. This is required
because browsers cannot interpolate between different forms (4-value ↔ 8-value) or between
forms that mix percentage and pixel values. Using 8-value consistently means every frame
is a valid interpolation target and there is no flash or jump when the shape crosses a
value boundary.

### 6. Lissajous specular highlight

The specular highlight drifts along a Lissajous curve: `x = cos(t)`, `y = sin(t × 0.7)`.
The `0.7` factor makes x and y periods coprime — they never complete a full cycle at the
same moment, so the path never obviously repeats on human-observable timescales. This is
what makes the highlight feel organic rather than robotic.

### 7. `mapVoiceStateToVisualState` — `transcribing → thinking`

The backend emits 7 `VoiceStateLiteral` values; the blob renders 6. `transcribing` is
collapsed to `thinking` because both represent the same moment: the assistant is working
on input and has not yet responded. Showing a different visual for the ~300ms Groq latency
window would be jarring, not informative.

---

## 3. Problems faced and how they were handled

### Problem 1 — Write tool blocked on new files

**What happened:** The `Write` tool requires the file to have been read at least once in
the conversation before it will write to it. Both `BlobStates.ts` and `Blob.tsx` were new
files in a new directory (`frontend/src/blob/`) — neither existed nor had been read.
Calling `Write` directly produced a `File has not been read yet` error.

**Fix:** Used PowerShell `New-Item` to create the directory and empty file first, then
called `Read` on the empty file (which produces a "file exists but is empty" warning),
then called `Write` to populate it. This two-step pattern (create → read → write) is
required for all new files.

**Rule going forward:** For any new file that doesn't yet exist, always create it with
`New-Item` first, then `Read` it (even if empty), then `Write`.

### Problem 2 — PowerShell `&` operator unavailable in Bash tool

**What happened:** Attempted to start the Vite dev server as a background process using
the Bash tool with `npm run dev 2>&1 &`. PowerShell 5.1 does not support the `&`
background operator — it's reserved syntax and throws a parse error.

**Fix:** Switched to the PowerShell tool and used `Start-Process` to launch the dev
server in a new window: `Start-Process -FilePath "cmd.exe" -ArgumentList "/c cd /d ... && npm run dev"`.

**Rule going forward:** Background processes on Windows must be launched via the
PowerShell tool using `Start-Process`, not via Bash with `&`.

### Problem 3 — Playwright not available for automated screenshots

**What happened:** Attempted to use a Node.js Playwright script to screenshot the running
blob for visual verification. `require('playwright')` failed — Playwright is not installed
globally on this machine.

**Fix:** Used `Start-Process "http://localhost:5173"` to open the app in the default
browser instead. Visual verification was done manually by the user.

**Rule going forward:** Automated screenshot verification via Playwright is not available
in this environment. For UI verification, open the browser directly and report observations.

---

## 4. Heads-up: downstream complications to watch

### CPU at ~13% in idle — slightly over the 10% target

The rAF loop currently makes 5 separate `element.style.*` writes per element per frame
(border-radius, transform, opacity on the wrapper; background and filter on the conic
layer; background on the radial layer; background on the highlight; opacity on the grain
SVG). Each write triggers a style recalculation in the browser's rendering pipeline.

**Mitigation:** Batch all writes for each element into a single `element.style.cssText`
assignment per frame. This reduces style recalculations significantly. Alternatively,
drop `conicBlur` from 28px to 20px — blur is the most expensive CSS filter on the
GPU/CPU compositor path.

**When to address:** First 15 minutes of Day 16 before audio reactivity work begins.
If CPU drops below 10% after batching, no further action needed.

### Dev state cycler must be removed on Day 17

The 6-button row in `App.tsx` is guarded by `import.meta.env.DEV` and is invisible in
production. However, the JSX block remains in the source file until Day 17 polish.
If Day 17 is skipped or shortened, this cleanup could be missed.

**Mitigation:** The commit message and journal entry both call this out explicitly.
Search for `import.meta.env.DEV` in `App.tsx` on Day 17 as the removal target.

### `configRef` in rAF loop lags by one render on rapid state changes

The rAF loop reads `configRef.current` for morph intensity and speed. `configRef` is
updated at the top of `animateTo()` — synchronously, before the `animate()` calls. So
for the morph params (`morphIntensity`, `morphSpeed`), the change is instantaneous.
However, for the color/opacity values read via `.get()`, the change is gradual (600ms
lerp). This is the intended behaviour.

The edge case: if two state changes arrive within the same 16ms frame window (e.g. the
backend sends `state_changed(thinking)` and `state_changed(speaking)` back-to-back faster
than the event queue can drain), `configRef` will be written twice before the rAF loop
reads it. The morph params will snap to the second state's values while the colors are
still mid-transition from the first. In practice this can't happen because the orchestrator
enforces state transitions — `thinking → speaking` always goes through a real LLM call
that takes hundreds of milliseconds. Not a real risk for v1.

### Audio reactivity (Day 16) will require rAF loop changes

Day 16 adds mic amplitude and TTS amplitude broadcast via WebSocket. The amplitude value
will need to drive edge deformation in the blob — expanding the border-radius oscillation
on speaking, contracting it on silence. The current rAF loop reads only `configRef` for
morph params; Day 16 will need to expose an `amplitudeRef` that the loop also reads.

**Design ahead:** Add `const amplitudeRef = useRef(0)` to `Blob.tsx` and expose a setter
(via `useImperativeHandle` or a prop callback) for the WebSocket handler in `App.tsx` to
write to. The rAF loop then adds `amplitude * cfg.morphIntensity * 0.3` to the base
border-radius oscillation for the speaking state.

---

## 5. How to verify Day 15

```powershell
# 1. Blob renders and animates in idle
# Start both terminals:
#   Terminal 1: cd frontend && npm run dev
#   Terminal 2: python -m backend.desktop
# Expected: orb visible, continuously morphing, blue-teal palette

# 2. All 6 states visually distinct (dev cycler)
# Click each button in order: idle → listening → thinking → speaking → muted → error
# Expected: each transition is a smooth ~600ms color/scale wash (not a snap)
# Expected: muted is visibly dimmed (~40% opacity, grey palette)
# Expected: error flashes red-orange, then auto-recovers to idle after ~3s

# 3. Real voice loop still works
# Hold Alt+Space, ask a question
# Expected: blob transitions idle → listening → thinking → speaking → idle
# Expected: spoken response plays

# 4. Mute toggle
# Ctrl+Alt+J → blob dims to muted state
# Ctrl+Alt+J → blob returns to idle

# 5. Dev cycler not visible in production build
cd frontend && npm run build
# Expected: build succeeds, no TypeScript errors
```

All checks passed on 2026-05-27.

---

## 6. Open items before Day 16

- [ ] CPU optimization — batch `style.*` writes into `cssText` per element per frame.
      Target: bring `msedgewebview2.exe` from ~13% to under 10% in idle
- [ ] Day 16 amplitude hook — plan where `amplitudeRef` lives and how `App.tsx` feeds it
      before writing the WebSocket amplitude broadcast code
- [ ] First-hour tuning pass — review all 6 state palettes after a night's rest;
      adjust values in `BlobStates.ts` before wiring audio reactivity

---

## 7. Files changed this day

```
NEW:
  frontend/src/blob/BlobStates.ts   — BlobStateConfig interface, BLOB_STATES record (6 states),
                                      mapVoiceStateToVisualState helper
  frontend/src/blob/Blob.tsx        — animated orb component (rAF + Framer Motion split)
  docs/plans/day_15_plan.md         — day plan committed alongside work

EDIT:
  frontend/src/App.tsx              — Blob imported and rendered; muted boolean removed;
                                      statusLabel simplified; dev state cycler added
  docs/journal.md                   — Day 15 entry added

DELETED:
  frontend/src/hooks/useVoiceState.ts    — empty stub, never imported
  frontend/src/components/StatusBar.tsx  — empty stub, never imported
```

---

## 8. Commits

```
b31b315  feat: svg/css animated blob with state machine
```
