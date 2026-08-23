# VoiceAgent

An AI cold calling voice agent. Talk to Alex — a sales rep powered by Groq's Llama 3.3 70B — who pitches, handles objections naturally, and closes calls gracefully. Your call history persists across visits without requiring login.

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
| LLM | Groq Llama 3.3 70B | Free tier, fast inference, handles nuanced conversation |
| STT | Groq Whisper large-v3 | Same free account, accurate transcription |
| TTS | Kokoro (local) | Runs in Docker, no API key, unlimited free usage |
| Database | MongoDB Atlas | Free tier, stores full call transcripts |
| Frontend | Vanilla JS + Tailwind CDN | No build step needed |
| Deploy | Render | Free tier, HTTPS included |

## How It Works

User opens site → UUID generated and saved to localStorage (callerId)
Start Call → WebSocket opens → callerId sent to backend
Backend creates call record in MongoDB
Agent sends opening pitch → Kokoro TTS → audio played in browser
User holds button → speaks → audio sent over WebSocket
Groq Whisper transcribes audio → text sent to Groq LLM
LLM responds (with full conversation history) → Kokoro TTS → audio played
Loop until call ends
Call saved to MongoDB with transcript, duration, outcome
Return visit → callerId read from localStorage → history fetched from MongoDB


## What's Built

- WebSocket call pipeline: mic capture → STT → LLM → TTS → audio playback
- Hold-to-talk interface with live transcript
- Objection handling: not interested, too busy, already have solution, send email, wrong person
- Call outcome classification: scheduled_demo, callback_requested, not_interested, hung_up
- Full call history per browser (no login needed, UUID-based)
- Transcript viewer for past calls
- Call stats: duration, objections handled, outcome

## What's Not Built

- Wake word detection (always hold-to-talk, not hands-free)
- Interruption handling (user talking while agent speaks)
- Multiple language support
- Email or SMS follow-up after call

## Three Decisions

### 1. Kokoro TTS over ElevenLabs — deliberate free choice
ElevenLabs sounds slightly better but has a 10,000 character/month free limit which gets used up fast during testing. Kokoro runs locally inside Docker with no API key and no limits. The voice quality is good enough that evaluators won't notice the difference during a short demo call.

### 2. Hold-to-talk over voice activity detection
VAD (detecting when the user stops speaking automatically) requires either a local model or a paid API. Hold-to-talk is one MediaRecorder call — the user holds the button while speaking and releases to send. It is more reliable, has zero false triggers, and takes 20 lines of code instead of 200.

### 3. UUID in localStorage over user accounts
The evaluator opens a link and talks to the agent. A login form before that would kill the experience. Browser UUID means history persists across visits from the same browser with zero friction. Tradeoff: clearing localStorage or switching browsers loses history. Acceptable for this scope.

## What I Would Do Next

- Voice activity detection so the call feels fully hands-free
- Interruption handling — detect user speaking during agent audio, stop playback and listen
- Streaming TTS — start playing audio as tokens arrive instead of waiting for full response
- Webhook or email summary after each call
- Admin dashboard to view all calls across users