# Day 15 Plan — SVG/CSS Animated Blob

**Week 3, Day 1.** First day of the visual presence. The orb becomes the assistant's face.

---

## Goal for the day

Replace the placeholder cyan test circle with an animated SVG/CSS blob that visually reflects all six voice states. Composition: path morphing (breathing silhouette) + slow conic-gradient color turn + drifting specular highlight + grainy watercolor texture overlay. Palette is sampled from the ElevenLabs reference video. Edge deformation is intentionally deferred to Day 16 where it will be driven by audio amplitude.

By end of day: the orb renders smoothly inside PyWebView, all six states (`idle`, `listening`, `thinking`, `speaking`, `muted`, `error`) are visually distinct, transitions between states are smooth (Framer Motion-interpolated, not snap), and CPU usage stays under 10% while idle.

---

## Design parameters (locked from the prototyping session)

| Parameter | Value | Notes |
|---|---|---|
| Silhouette technique | Path morphing via animated `border-radius` | Not SVG path `d` morphing — cheaper, no `flubber` dependency |
| Morph intensity (idle) | 10 | Out of 0–15 range; visibly blob-like, not amoeba |
| Color turn technique | Rotating blurred `conic-gradient` | Behind the radial gradient layer |
| Conic rotation speed (idle) | 1.0 deg/frame | ~6s per full turn at 60fps |
| Specular highlight | Drifting soft circle, no twinkles | Material/polished feel, not magical/celestial |
| Highlight opacity | 0.45 | |
| Grain | SVG `feTurbulence` static overlay | Not animated — paper-like static texture |
| Grain opacity | 0.65 | Blend mode `overlay` |
| Edge deformation | Deferred to Day 16 | Will be driven by audio amplitude |
| Palette anchor | Watercolor blue → teal → forest green | Sampled from reference video frames |

**Idle palette (hex):**
- Light highlight cyan: `#B5E8F8`
- Mid sky blue: `#5BACC8`
- Deep blue: `#3D8EAD`
- Dark blue-teal: `#1F6D86`
- Forest green: `#2F4B2F`
- Olive: `#507A59`
- Deep forest: `#1F4332`

---

## Pre-flight cleanup (do these first, in any order)

These are tiny but worth doing before the blob lands so the diff stays clean.

### CL-1. Delete the placeholder test shape in `App.tsx`

Remove the `<div className="w-24 h-24 rounded-full bg-cyan-400/50" />` and its comment. The blob will replace it.

### CL-2. Resolve the redundant `muted` boolean

`App.tsx` currently has `const [muted, setMuted] = useState(false)` plus logic in the dispatch effect that derives `muted` from `state_changed.state === "muted"`. Since `voiceState` is now the source of truth:

- Delete `const [muted, setMuted] = useState(false)`
- Delete the two `setMuted` calls inside the `state_changed` branch of the dispatch effect
- Replace `muted` in `statusLabel` with `voiceState === "muted"`
- Tighten `statusLabel`: with the blob conveying state visually, the textual badge becomes a debug aid. Keep it for Day 15 (safety net while the blob is new), plan to demote/remove on Day 17 polish.

### CL-3. (Optional) Delete the empty `useVoiceState.ts` and `StatusBar.tsx`

Both files exist but are empty and unimported. If you want to keep the repo tidy: `git rm` them. If you'd rather leave the architecture skill's filenames as forward-references to future files, leave them. No code impact either way.

---

## Tasks

### T-1. Create `BlobStates.ts` — the visual config per state

**File:** `frontend/src/blob/BlobStates.ts` (new)

This is a pure data file. No React, no Framer Motion. It exports:

- A `BlobStateConfig` TypeScript interface defining every tunable parameter
- A `BLOB_STATES` record mapping each of the six visual states to a config
- A `mapVoiceStateToVisualState` helper that collapses `transcribing` → `thinking`

**Why this lives in a separate file:** tuning the visuals (Day 16 morning, and any later polish day) means changing numbers, not code. Splitting the data from the component keeps tuning ergonomic and `Blob.tsx` short.

**Shape of `BlobStateConfig` (write this signature yourself before asking for implementation):**

```ts
interface BlobStateConfig {
  // Path morphing
  morphIntensity: number;    // 0-15; how far border-radius oscillates from 50%
  morphSpeed: number;        // multiplier on the sin() timebase; 1.0 = baseline

  // Conic gradient (color turn)
  conicSpeed: number;        // degrees per frame
  conicColors: string[];     // hex stops, looped
  conicBlur: number;         // px blur applied to the conic layer

  // Radial gradient overlays (the watercolor regions)
  // 3 soft regions at fixed positions, color and alpha vary per state
  radialColors: [string, string, string];

  // Specular highlight
  highlightOpacity: number;  // 0-1
  highlightSpeed: number;    // path traversal speed (0.005 = leisurely)

  // Grain
  grainOpacity: number;      // 0-1

  // Overall
  scale: number;             // 1.0 idle; listening ~1.05; muted ~0.95
  opacity: number;           // 1.0 idle; muted ~0.4
}
```

**Per-state config (first-pass values — tune on Day 16 morning):**

| State | morphInt | morphSpd | conicSpd | conicBlur | scale | opacity | grainOp | Palette character |
|---|---|---|---|---|---|---|---|---|
| `idle` | 10 | 1.0 | 1.0 | 28 | 1.0 | 1.0 | 0.65 | Blue → teal → forest (locked) |
| `listening` | 12 | 1.5 | 1.5 | 24 | 1.05 | 1.0 | 0.65 | Brighter, more saturated idle palette |
| `thinking` | 8 | 0.6 | 0.4 | 32 | 0.98 | 1.0 | 0.7 | Deeper, indigo-leaning |
| `speaking` | 11 | 1.2 | 1.2 | 26 | 1.02 | 1.0 | 0.6 | Idle palette + brighter highlight (amplitude on Day 16) |
| `muted` | 6 | 0.4 | 0.3 | 30 | 0.92 | 0.4 | 0.5 | Desaturated grayscale of idle |
| `error` | 14 | 2.0 | 2.5 | 22 | 1.0 | 1.0 | 0.65 | Reds and burnt oranges; auto-recover 3s |

These are first guesses. Don't burn time perfecting them today — get them visually distinct, lock in tuning during Day 16's first hour.

### T-2. Create `Blob.tsx` — the component

**File:** `frontend/src/blob/Blob.tsx` (new)

**Props:**

```ts
interface BlobProps {
  voiceState: VoiceStateLiteral;  // from useWebSocket
  size?: number;                  // default 180
}
```

**Internal structure (top to bottom in the DOM, back to front visually):**

1. **Outer wrapper** — fixed size, `border-radius` animated every frame from the current state's morph params. This is the silhouette. `overflow: hidden` clips children to the blob shape.
2. **Conic gradient layer** — absolutely positioned, slightly oversized (`inset: -30px`), heavily blurred. Rotates every frame.
3. **Radial gradient layer** — three soft color regions at fixed positions, `mix-blend-mode: screen`. Colors come from `radialColors`.
4. **Specular highlight** — small soft white radial, position driven by a slow Lissajous curve (different periods on x and y).
5. **Grain overlay** — an inline SVG with `feTurbulence` filter, static seed, `mix-blend-mode: overlay`, opacity from state config.

**Animation strategy — important:**

Two kinds of motion happening, handled differently:

- **Continuous per-frame motion** (morph border-radius, conic rotation, specular drift): `requestAnimationFrame` loop. Reading values directly from a `useRef` that points at the current state config.
- **Smooth interpolation between states** (color stops, scale, opacity, grain opacity changing when state flips): Framer Motion's `useMotionValue` + `animate()` to lerp between configs over ~600ms. The rAF loop reads the *current* motion-value reading each frame.

This split is what makes idle→thinking feel like a smooth wash of color change rather than a snap.

**One implementation gotcha to handle:** the rAF loop must clean up on unmount. Use the standard `cancelAnimationFrame` in the cleanup of a `useEffect`. Framer Motion's animations also need cleanup if you `.cancel()` them; not strictly necessary but tidy.

**Drag/click behaviour:** apply `WebkitAppRegion: "no-drag"` inline style on the outer wrapper. The drag bar in `App.tsx` is what moves the window; the blob itself should be clickable later (mute toggle on click is a Day 17 polish idea, but the property needs to be `no-drag` now to leave room for that).

### T-3. Wire `Blob` into `App.tsx`

**File:** `frontend/src/App.tsx`

Single-line import + single-line render in the content area where the test shape used to be. The blob takes `voiceState` directly from the `App.tsx` state, which is already wired to the dispatcher.

Place the blob *above* the status badge (which becomes debug-only) and the chat panel. Centered horizontally, with reasonable vertical spacing.

### T-4. Debug state cycler (dev-only)

**File:** `frontend/src/App.tsx` (inline addition, ~15 lines)

A small row of 6 buttons rendered only when `import.meta.env.DEV` is true. Each button force-sets `voiceState` to the corresponding state. This bypasses the WebSocket entirely and lets you cycle through all six visual states during testing without having to actually trigger PTT/mute/error each time.

Without this, testing the error state on Day 15 requires deliberately breaking the backend. Not worth that today.

The cycler is removed on Day 17 polish.

### T-5. Visual verification + tuning pass

Click through all 6 states using the debug cycler. For each:

1. Does it look visually distinct from the others at a glance? (If `listening` and `idle` look the same, `listening` isn't doing its job.)
2. Does the transition into it from `idle` feel smooth (no snapping)?
3. Does it stay enjoyable to look at for 30 seconds? (`error` doesn't need to — auto-recovers after 3s.)

Take notes on what's off. Don't fix tuning now — write it down for Day 16's first hour.

### T-6. Performance check

Open Windows Task Manager → Details tab → look for the PyWebView process. With the blob in `idle`, CPU should hold under 10%. If it's higher:

- Likely culprit: the rAF loop doing too much per frame. Profile briefly. Common fixes: reduce the number of `style` writes per frame (batch into a single template-literal write of `cssText` for the outer div), or drop the conic blur from 28px to 20px.
- If still bad: reduce the size of the grain SVG (smaller `baseFrequency` rect dimensions).

The i3 needs to stay cool when the blob is just sitting there.

---

## What's deliberately out of scope today

- **Audio reactivity** (mic amplitude → edge deformation, TTS amplitude → speaking pulse). Day 16.
- **Window polish** (snap-to-corner, settings panel, mic device picker). Day 17.
- **Tuning state palettes beyond "good first guess".** Day 16 morning.
- **Click handlers on the blob** (e.g. tap to mute). Day 17.
- **Removing the status badge entirely.** Day 17 polish; keep as debug aid for now.
- **Architecture skill updates** for the stale filename references (`useVoiceState.ts`, `StatusBar.tsx`). Worth doing on a polish day.

If you find yourself tempted to "just quickly add" any of these, stop and write a note in `docs/journal.md` instead.

---

## Manual test checklist (run at end of day)

For each test, the orb behaves as expected and the dev console has no errors.

1. App boots, blob renders in idle without flicker.
2. Click debug button for each of 6 states. Transition each time is smooth (~600ms lerp).
3. Hold Alt+Space, release. Blob transitions: idle → listening → thinking → speaking → idle. Each transition smooth, each visually distinct.
4. Hit Ctrl+Alt+J during idle. Blob fades to muted state. Hit again. Returns to idle.
5. Hit Ctrl+Alt+J during a turn (release PTT, then immediately mute mid-thinking). Blob shows muted state cleanly; no visual artifacts from the cancelled pipeline.
6. Open Task Manager. CPU on the WebView process is under 10% in idle. Note the actual number in journal.md.
7. Stare at the idle blob for 30 seconds. It still feels alive (motion is continuous, not looping obviously).

---

## Completion criteria

- [ ] `frontend/src/blob/BlobStates.ts` exists with `BlobStateConfig`, `BLOB_STATES` record (6 entries), and `mapVoiceStateToVisualState` helper
- [ ] `frontend/src/blob/Blob.tsx` exists, renders the orb, animates per the spec above
- [ ] `App.tsx` renders `<Blob voiceState={voiceState} />` where the test shape used to be
- [ ] Test cyan circle removed; redundant `muted` state removed; statusLabel simplified
- [ ] Debug state cycler appears only in dev mode and force-sets state correctly
- [ ] All 6 states visually distinct at a glance
- [ ] State transitions are interpolated (no snapping on state change)
- [ ] CPU under 10% in idle (record actual number in journal.md)
- [ ] No console errors
- [ ] You can explain the rAF + Framer Motion split (how continuous motion and state interpolation cooperate) to yourself without looking at the code

---

## Git commit message

```
feat: svg/css animated blob with state machine

Replaces placeholder test shape with full Blob component:
- Path morphing via animated border-radius (idle morph=10)
- Conic-gradient rotation behind soft radial color regions
- Drifting specular highlight on Lissajous curve
- Static feTurbulence grain overlay for watercolor texture
- 6 visual states (idle/listening/thinking/speaking/muted/error)
  with smooth Framer Motion-interpolated transitions
- transcribing collapsed to thinking visual
- Palette sampled from ElevenLabs orb reference

Drops redundant muted boolean (voiceState is source of truth).
Dev-only state cycler for testing all 6 states without triggering
real backend events.

Edge deformation deferred to Day 16 (audio reactivity).
```

---

## Time budget

| Phase | Estimate |
|---|---|
| Pre-flight cleanup (CL-1, CL-2, optional CL-3) | 20 min |
| T-1 `BlobStates.ts` | 45 min |
| T-2 `Blob.tsx` | 2.5 hours (the meat of the day) |
| T-3 Wire into `App.tsx` | 15 min |
| T-4 Debug cycler | 20 min |
| T-5 Visual verification + notes for Day 16 | 30 min |
| T-6 Performance check | 15 min |
| Buffer / unforeseen | 45 min |
| **Total** | **~5.5–6 hours** |

The Day-by-Day plan budgets 6 hours for Day 15. We're inside that.

---

## Workflow reminders

- **Write the docstring/signature first.** For `BlobStateConfig`, write the interface yourself in `BlobStates.ts` before asking for the rest of the file. For `Blob.tsx`, write the `BlobProps` interface and a stub function signature first. This is how you stay in the read-review loop.
- **Read every line before accepting.** The rAF + Framer Motion split is the trickiest bit — if any of it is unclear, ask for an explanation block-by-block before moving on.
- **Type one line yourself** for each non-trivial section. Your hands learning the idioms is the whole point.
- **Commit per task, not per day.** Six small commits beats one big one.

---

## When you get stuck

The most likely stuck-points and what to do:

- **`border-radius` string isn't animating smoothly:** check that you're writing the full 8-value form (`A% B% C% D% / E% F% G% H%`), not the 4-value form. Browsers don't interpolate between mixed forms.
- **Conic gradient looks like a visible rainbow stripe instead of soft color turn:** blur is too low or color stops are too saturated. Raise blur to 32px, desaturate stops.
- **Specular drift looks robotic (loops obviously):** the x and y periods need to be coprime. Use `Math.cos(t)` for x and `Math.sin(t * 0.7)` for y, not the same period for both.
- **CPU spikes when window is in background:** rAF still runs in background tabs in many browsers but throttles to 1Hz. PyWebView may not throttle. Acceptable for now; revisit on Day 17 if it bothers you.
- **`feTurbulence` looks pixelated/blocky:** raise `numOctaves` to 3, lower `baseFrequency` to 0.65–0.85.

If you're stuck for more than 30 minutes on any one thing, paste the error or symptom with the relevant code snippet and ask. Don't grind.
