import os
import io
import numpy as np
import soundfile as sf
from groq import AsyncGroq

# Kokoro TTS — runs locally, no API key, unlimited free usage.
# We lazy-load it on first use so startup is fast.
# If kokoro fails to load (e.g. missing espeak-ng), we fall back to
# a simple error message rather than crashing the whole server.

_kokoro_pipeline = None

def get_tts_pipeline():
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        try:
            from kokoro import KPipeline
            # lang_code='a' = American English
            _kokoro_pipeline = KPipeline(lang_code='a')
            print("[tts] Kokoro pipeline loaded")
        except Exception as e:
            print(f"[tts] Kokoro failed to load: {e}")
            _kokoro_pipeline = None
    return _kokoro_pipeline

def text_to_speech(text: str) -> bytes:
    """
    Converts text to audio bytes (WAV format) using Kokoro TTS.
    Returns raw WAV bytes that the frontend plays directly.
    If Kokoro is unavailable, returns empty bytes and logs the error.
    """
    if not text or not text.strip():
        return b""

    pipeline = get_tts_pipeline()
    if pipeline is None:
        print("[tts] pipeline not available, skipping audio")
        return b""

    try:
        # Kokoro returns a generator of (graphemes, phonemes, audio_array) tuples
        # voice='af_heart' is a natural-sounding American female voice
        audio_chunks = []
        for _, _, audio in pipeline(text, voice='af_heart', speed=1.0):
            if audio is not None:
                audio_chunks.append(audio)

        if not audio_chunks:
            return b""

        # Concatenate all chunks into one audio array
        full_audio = np.concatenate(audio_chunks)

        # Write to WAV bytes buffer — 24kHz sample rate (Kokoro default)
        buffer = io.BytesIO()
        sf.write(buffer, full_audio, 24000, format='WAV')
        buffer.seek(0)
        return buffer.read()

    except Exception as e:
        print(f"[tts] error: {e}")
        return b""

# Groq Whisper STT — same Groq account, free tier
_groq_client = None

def get_groq_client():
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    return _groq_client

async def speech_to_text(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """
    Sends audio bytes to Groq Whisper and returns transcribed text.
    The browser sends audio/webm from MediaRecorder — Whisper handles it natively.
    Returns empty string on failure rather than raising — a failed transcription
    should not crash the call, the agent will ask the user to repeat.
    """
    if not audio_bytes:
        return ""

    try:
        client = get_groq_client()

        # Groq expects a file-like object with a name so it knows the format
        audio_file = ("audio.webm", io.BytesIO(audio_bytes), mime_type)

        transcription = await client.audio.transcriptions.create(
            file=audio_file,
            model="whisper-large-v3",
            language="en",
            response_format="text"
        )

        text = transcription.strip() if isinstance(transcription, str) else transcription.text.strip()
        print(f"[stt] transcribed: {text}")
        return text

    except Exception as e:
        print(f"[stt] error: {e}")
        return ""
