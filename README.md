# VoiceAgent

An AI cold calling voice agent. Talk to Alex — a sales rep powered by Groq's `openai/gpt-oss-120b` — who pitches, handles objections, and closes calls gracefully. Your call history persists across visits without requiring login.

## Live Demo
https://voiceagent-sl69.onrender.com

## How to Run Locally

**Requirements:** Docker and Docker Compose.

1. Get a free Groq API key at console.groq.com (no credit card)
2. Get a free MongoDB Atlas connection string at cloud.mongodb.com
3. Clone and run:

```bash
git clone https://github.com/Hassanahmed52/VoiceAgent.git
cd VoiceAgent
cp .env.example .env
# Edit .env and add your GROQ_API_KEY and MONGODB_URI
docker-compose up --build
```

Open http://localhost:8000

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Backend | Python + FastAPI | Native async, clean WebSocket support |
| LLM | Groq `openai/gpt-oss-120b` | Free tier, fast inference; switched from Llama 3.3 70B after Groq decommissioned it mid-project |
| STT | Groq Whisper large-v3 | Same free account, accurate transcription |
| TTS | gTTS (Google Text-to-Speech) | Runs with ~5MB memory footprint — Render free tier only has 512MB RAM, so heavier local TTS wasn't viable |
| Database | MongoDB Atlas | Free tier, stores full call transcripts |
| Frontend | Vanilla JS + Tailwind CDN | No build step needed |
| Deploy | Render | Free tier, HTTPS included |

## How It Works

User opens site → UUID generated and saved to localStorage (callerId)
Start Call → WebSocket opens → callerId sent to backend
Backend creates call record in MongoDB
Agent sends opening pitch → gTTS → audio played in browser
User holds button → speaks → audio sent over WebSocket
Groq Whisper transcribes audio → text sent to Groq LLM
LLM responds (with full conversation history) → gTTS → audio played
Loop until call ends
Call saved to MongoDB with transcript, duration, outcome
Return visit → callerId read from localStorage → history fetched from MongoDB

## What's Built

- WebSocket call pipeline: mic capture → STT → LLM → TTS → audio playback
- Hold-to-talk interface with live transcript
- Objection handling: not interested, too busy, already have solution, send email, wrong person
- Call outcome classification: scheduled_demo, callback_requested, not_interested, hung_up
- Full call history per browser (no login needed, UUID-based)
- Delete a single call, or clear all history, from the browser
- Transcript viewer for past calls
- Call stats: duration, objections handled, outcome

## What's Not Built

- Wake word detection (always hold-to-talk, not hands-free)
- Interruption handling (user talking while agent speaks)
- Multiple language support
- Email or SMS follow-up after call

## Problems I Hit (and How They Were Fixed)

This project went through a lot of real debugging, not just first-pass code. Roughly in order:

1. **Git identity not set** — configured `user.email` / `user.name` locally.
2. **motor 3.5.0 + pymongo 4.17.0 incompatible** — a `_QUERY_OPTIONS` import error broke the DB layer. Pinned to `motor==3.3.2` + `pymongo==4.6.3`.
3. **`Cannot GET /`** — static file path was wrong in the Dockerfile. Fixed by copying `frontend/` to `/frontend` in the image and serving from an absolute path.
4. **Kokoro TTS crashed the deploy** — Kokoro pulls in PyTorch (~500MB) and Render's free tier caps at 512MB RAM. Replaced with gTTS (~5MB footprint).
5. **`if call_id and db`** — PyMongo raises `NotImplementedError` on `bool(db)`. Changed all such checks to explicit `is not None`.
6. **Mixed naive/aware datetimes** — `datetime.utcnow()` and `datetime.now(timezone.utc)` were used inconsistently, causing subtract errors. Standardized on `datetime.utcnow()` everywhere.
7. **`mediaRecorder.mimeType` was null on stop** — a module-level variable was being nulled before the `onstop` callback fired. Fixed by capturing the recorder in a local closure variable instead.
8. **`btoa` crashed on large audio blobs** — replaced with `FileReader.readAsDataURL`, which handles large binary safely.
9. **Render's CDN cached an old JS file** — renamed `call.js` → `call.v2.js` to force a fresh fetch.
10. **Groq model decommissioned mid-project** — `llama-3.3-70b-versatile`, then `llama3-70b-8192`, were both retired by Groq within weeks of each other. Landed on `openai/gpt-oss-120b`, one of Groq's current recommended models.
11. **`reasoning_effort` TypeError** — `gpt-oss` models support a `reasoning_effort` param on Groq, but the pinned `groq==0.9.0` SDK predated it. Bumped to `groq==0.31.0`.
12. **Empty/truncated LLM replies** — `gpt-oss` is a reasoning model: it spends tokens on hidden reasoning before writing the actual reply, and both count against `max_tokens`. On harder turns (objections, ambiguous input) it would burn the whole budget reasoning and return `''`, or get cut off mid-sentence. Fixed with `reasoning_effort="low"` and a higher `max_tokens` (500).
13. **Calls ending after a single exchange** — the model would tag an `OUTCOME` far too eagerly (`gpt-oss` is "eager" about resolving open loops). Added a code-level guard requiring a minimum number of user turns before any outcome is honored (except a real hang-up).
14. **`scheduled_demo` firing without real agreement** — the model would propose a time *and* write the user's "yes" for them in the same turn, then close the call on its own hallucinated confirmation. Fixed by requiring the model to propose and confirm in two separate turns via the prompt, plus a hard code-level check (`user_confirmed_demo()`) that only trusts confirmation language actually present in the user's real transcribed message.
15. **Scheduling friction misread as rejection** — "I'm busy Thursday, try another time" was being counted as an objection and eventually flipped the outcome to `not_interested`, even though the user was still actively trying to book. Split "reschedule" language from "rejection" language and added a matching hard gate (`user_rejected_offer()`).
16. **Profanity-laden rejections slipping through the gate** — blunt blow-offs ("fuck off", "you're a loser, end the call") weren't matching the rejection phrase list, so the call kept going for several more turns after a clearly hostile rejection. Expanded the rejection phrase list to catch this language directly.
17. **Confirmed rejections still forced through the minimum-turns guard** — an unambiguous "I'm not interested, end the call" on an early turn was still being blocked by the turn-count safety net meant for premature/ambiguous outcomes. Reordered the logic so explicit content checks run first, and a confirmed rejection is exempted from the turn minimum.

The overall approach: don't trust the LLM's self-reported signals (outcome tags, "the user agreed") at face value. Every outcome that ends a call is now cross-checked against what the user *actually said*, in code, before it's honored — the model can suggest an outcome, but the code has final say.

## Three Decisions

### 1. gTTS over ElevenLabs / Kokoro
ElevenLabs has a tight free character limit that gets eaten fast during testing. Kokoro sounds better and needs no API key, but its PyTorch dependency exceeded Render's free-tier RAM and crashed the deploy. gTTS is the pragmatic middle ground: no API key, tiny memory footprint, good enough quality for a demo call.

### 2. Hold-to-talk over voice activity detection
VAD (auto-detecting when the user stops speaking) needs either a local model or a paid API. Hold-to-talk is one `MediaRecorder` call — the user holds the button while speaking and releases to send. More reliable, zero false triggers, far less code.

### 3. UUID in localStorage over user accounts
The evaluator opens a link and talks to the agent. A login form before that would kill the experience. Browser UUID means history persists across visits from the same browser with zero friction. Tradeoff: clearing localStorage or switching browsers loses history — acceptable for this scope, and now mitigated somewhat by the option to export/clear history intentionally rather than losing it by accident.

## What I Would Do Next

- Voice activity detection so the call feels fully hands-free
- Interruption handling — detect user speaking during agent audio, stop playback and listen
- Streaming TTS — start playing audio as tokens arrive instead of waiting for full response
- Webhook or email summary after each call
- Admin dashboard to view all calls across users
- Revisit the "never admit I'm a bot" behavior against AI-voice-disclosure requirements before any real-world (non-demo) use
