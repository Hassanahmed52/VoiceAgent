import os
import io
import asyncio
from groq import AsyncGroq

# Cache for pre-generated audio — avoids cold gTTS call on first user connect
_audio_cache = {}

def text_to_speech(text: str) -> bytes:
    if not text or not text.strip():
        return b""

    # Return cached audio if available
    if text in _audio_cache:
        print(f"[tts] cache hit for: {text[:40]}")
        return _audio_cache[text]

    try:
        from gtts import gTTS
        from pydub import AudioSegment

        tts = gTTS(text=text, lang="en", slow=False, tld="com")
        mp3_buffer = io.BytesIO()
        tts.write_to_fp(mp3_buffer)
        mp3_buffer.seek(0)

        audio = AudioSegment.from_mp3(mp3_buffer)
        wav_buffer = io.BytesIO()
        audio.export(wav_buffer, format="wav")
        wav_buffer.seek(0)
        result = wav_buffer.read()

        # Cache it
        _audio_cache[text] = result
        print(f"[tts] generated {len(result)} bytes for: {text[:40]}")
        return result

    except Exception as e:
        print(f"[tts] error: {e}")
        return b""

_groq_client = None

def get_groq_client():
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    return _groq_client

async def speech_to_text(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    if not audio_bytes or len(audio_bytes) < 500:
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
        print(f"[stt] transcribed: '{text}'")
        return text

    except Exception as e:
        print(f"[stt] error: {e}")
        return ""
