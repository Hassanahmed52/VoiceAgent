import os
import io
import asyncio
from groq import AsyncGroq

# TTS: gTTS — sends text to Google, gets MP3 back, converts to WAV
# Uses ~5MB RAM vs Kokoro's 500MB. No API key needed.
# Tradeoff: requires internet to Google's TTS servers (always available).

def text_to_speech(text: str) -> bytes:
    """
    Converts text to WAV audio bytes using Google TTS.
    Returns empty bytes on failure — never crashes the call.
    """
    if not text or not text.strip():
        return b""

    try:
        from gtts import gTTS
        from pydub import AudioSegment

        # Generate MP3 from Google TTS
        tts = gTTS(text=text, lang="en", slow=False, tld="com")
        mp3_buffer = io.BytesIO()
        tts.write_to_fp(mp3_buffer)
        mp3_buffer.seek(0)

        # Convert MP3 to WAV so browser AudioContext can decode it
        audio = AudioSegment.from_mp3(mp3_buffer)
        wav_buffer = io.BytesIO()
        audio.export(wav_buffer, format="wav")
        wav_buffer.seek(0)
        return wav_buffer.read()

    except Exception as e:
        print(f"[tts] error: {e}")
        return b""

# STT: Groq Whisper — unchanged
_groq_client = None

def get_groq_client():
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    return _groq_client

async def speech_to_text(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """
    Sends audio to Groq Whisper, returns transcribed text.
    Returns empty string on failure so the call continues.
    """
    if not audio_bytes or len(audio_bytes) < 1000:
        return ""

    try:
        client = get_groq_client()
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
