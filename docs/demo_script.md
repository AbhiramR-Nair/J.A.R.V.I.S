# Manual Demo Script

Run this checklist after any change to voice, LLM, memory, or prompt paths.
All probes can be done via PTT or `POST /chat`. Check all boxes before committing.

---

## 1. Voice + persona (Day 13)

- [ ] "What time is it?" → short reply ending in "sir", no preamble, no "Certainly!"
- [ ] "Tell me a joke." → dry, brief, British register — not a knock-knock
- [ ] "Switch to the kinase project." → curt acknowledgement, no filler
- [ ] "I'm a bit tired today." → brief acknowledgement + offered lighter pace
- [ ] Multi-turn: "Tell me about T315I." → concise technical answer + offered follow-up.
      Then "And how does Ponatinib differ?" → persona holds across the second turn
- [ ] Five consecutive replies contain no "Certainly!", "I'd be happy to", "Of course!", "Great question!"
- [ ] `GET /debug/system-prompt` returns 6 sections assembled in order (length_chars ~5300+)

## 2. Voice quality (Day 13)

- [ ] TTS voice is clearly British male register (en_GB-alan-medium)
- [ ] No chipmunk or slow-motion pitch artifacts
- [ ] No regression in TTS latency vs. previous session

## 3. PTT voice loop regression (Day 11-12)

- [ ] Hold Alt+Space, ask "What's the capital of France?", release → spoken answer within ~5s
- [ ] Mute toggle (Ctrl+Alt+J) stops TTS mid-speech
- [ ] Mute → PTT → unmute cycle returns to IDLE cleanly
- [ ] State badge in UI tracks: idle → listening → transcribing → thinking → speaking → idle

## 4. Audio robustness (Day 12)

- [ ] `POST /audio/device` with index 999 → HTTP 400 (invalid device rejected)
- [ ] Settings panel "Test mic" → green badge with peak amplitude after speaking
- [ ] Settings panel "Test mic" → yellow "Silent" badge when quiet

## 5. Memory + persistence (Day 11)

- [ ] After a factual exchange, ask "What did we just discuss?" → references prior context
- [ ] `SELECT COUNT(*) FROM memory ORDER BY id DESC LIMIT 5;` shows recent entries in jarvis.db

## 6. Tool-calling — Week 4 (Days 20–26)

- [ ] "What are the latest papers on ABL1 inhibitors?" → web search fires → sources block renders in UI → spoken answer
- [ ] "Open VS Code" → VS Code launches → spoken "Opening Visual Studio Code, sir." (#7)
- [ ] "Set a timer for 1 minute" → spoken confirmation → toast fires after 60 s → spoken completion when loop is idle (#8)
- [ ] Unknown app (e.g. "Open Photoshop") → soft-error spoken response, no crash
- [ ] Two overlapping timers → both toasts fire, both spoken completions heard

---

## End-of-week milestones

- **Week 2 (Day 14):** "I can hold Alt+Space, ask 'what's the capital of France?', and hear a spoken answer within 4 seconds."
- **Week 3 (Day 19):** "The blob looks alive — it reacts to my voice and changes state visibly."
- **Week 4 (Day 30):** "I used Jarvis for actual work today — summarised a paper, searched the web, logged a note, opened an app."
