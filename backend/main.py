import os
import json
import base64
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

from db import connect_db, close_db, get_db
from agent import get_agent_response, extract_outcome, count_objections, OPENING_PITCH
from speech import text_to_speech, speech_to_text

@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()

app = FastAPI(lifespan=lifespan)

# --- REST routes ---

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/calls")
async def get_calls(callerId: str):
    """
    Returns all completed calls for a given callerId (browser UUID).
    Used by index.html to show call history on return visits.
    """
    if not callerId:
        return JSONResponse(status_code=400, content={"message": "callerId required"})

    db = get_db()
    cursor = db.calls.find(
        {"callerId": callerId, "status": "completed"},
        sort=[("startTime", -1)]
    )
    calls = []
    async for call in cursor:
        call["_id"] = str(call["_id"])
        calls.append(call)

    return {"success": True, "data": {"calls": calls}}

@app.get("/api/calls/{call_id}")
async def get_call(call_id: str):
    """
    Returns a single call with full transcript.
    Used by transcript.html.
    """
    from bson import ObjectId
    db = get_db()

    try:
        call = await db.calls.find_one({"_id": ObjectId(call_id)})
    except Exception:
        return JSONResponse(status_code=400, content={"message": "Invalid call ID"})

    if not call:
        return JSONResponse(status_code=404, content={"message": "Call not found"})

    call["_id"] = str(call["_id"])
    return {"success": True, "data": {"call": call}}

# --- WebSocket ---

@app.websocket("/ws/call")
async def websocket_call(websocket: WebSocket):
    """
    Main call WebSocket. Protocol:
    
    Client → Server (JSON):
      { "type": "start", "callerId": "uuid" }
      { "type": "audio", "data": "<base64 encoded webm audio>" }
      { "type": "end" }
    
    Server → Client (JSON):
      { "type": "transcript", "role": "agent"|"user", "text": "..." }
      { "type": "audio", "data": "<base64 encoded wav>" }
      { "type": "status", "status": "listening"|"processing"|"speaking" }
      { "type": "call_ended", "outcome": "...", "callId": "..." }
      { "type": "error", "message": "..." }
    """
    await websocket.accept()

    db = get_db()
    call_id = None
    conversation_history = []
    caller_id = None

    try:
        # Wait for start message
        raw = await websocket.receive_text()
        msg = json.loads(raw)

        if msg.get("type") != "start":
            await websocket.send_text(json.dumps({"type": "error", "message": "Expected start message"}))
            return

        caller_id = msg.get("callerId", "anonymous")

        # Create call record in MongoDB
        call_doc = {
            "callerId": caller_id,
            "startTime": datetime.now(timezone.utc),
            "status": "active",
            "transcript": [],
            "objectionCount": 0,
            "outcome": None
        }
        result = await db.calls.insert_one(call_doc)
        call_id = str(result.inserted_id)

        # Send opening pitch
        await websocket.send_text(json.dumps({"type": "status", "status": "speaking"}))

        # Generate TTS for opening in a thread so we don't block the event loop
        audio_bytes = await asyncio.get_event_loop().run_in_executor(
            None, text_to_speech, OPENING_PITCH
        )

        # Send transcript first so user can read along
        await websocket.send_text(json.dumps({
            "type": "transcript",
            "role": "agent",
            "text": OPENING_PITCH
        }))

        # Send audio as base64
        if audio_bytes:
            await websocket.send_text(json.dumps({
                "type": "audio",
                "data": base64.b64encode(audio_bytes).decode()
            }))

        # Save opening to DB
        await db.calls.update_one(
            {"_id": result.inserted_id},
            {"$push": {"transcript": {
                "role": "agent",
                "text": OPENING_PITCH,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }}}
        )

        # Add to conversation history for LLM
        conversation_history.append({"role": "assistant", "content": OPENING_PITCH})

        await websocket.send_text(json.dumps({"type": "status", "status": "listening"}))

        # Main conversation loop
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg.get("type") == "end":
                # User clicked End Call
                break

            if msg.get("type") == "audio":
                await websocket.send_text(json.dumps({"type": "status", "status": "processing"}))

                # Decode base64 audio from browser
                audio_data = base64.b64decode(msg["data"])

                # Transcribe with Groq Whisper
                user_text = await speech_to_text(audio_data)

                if not user_text:
                    # Could not transcribe — ask user to repeat
                    await websocket.send_text(json.dumps({"type": "status", "status": "listening"}))
                    continue

                # Send user transcript to frontend
                await websocket.send_text(json.dumps({
                    "type": "transcript",
                    "role": "user",
                    "text": user_text
                }))

                # Save to DB
                await db.calls.update_one(
                    {"_id": result.inserted_id},
                    {"$push": {"transcript": {
                        "role": "user",
                        "text": user_text,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }}}
                )

                # Add to conversation history
                conversation_history.append({"role": "user", "content": user_text})

                # Get agent response from Groq LLM
                await websocket.send_text(json.dumps({"type": "status", "status": "speaking"}))
                agent_text_raw = await get_agent_response(conversation_history)

                # Extract OUTCOME if this is the final message
                agent_text, outcome = extract_outcome(agent_text_raw)

                # Send agent transcript
                await websocket.send_text(json.dumps({
                    "type": "transcript",
                    "role": "agent",
                    "text": agent_text
                }))

                # Generate and send TTS audio
                audio_bytes = await asyncio.get_event_loop().run_in_executor(
                    None, text_to_speech, agent_text
                )
                if audio_bytes:
                    await websocket.send_text(json.dumps({
                        "type": "audio",
                        "data": base64.b64encode(audio_bytes).decode()
                    }))

                # Save agent response to DB
                await db.calls.update_one(
                    {"_id": result.inserted_id},
                    {"$push": {"transcript": {
                        "role": "agent",
                        "text": agent_text,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }}}
                )

                # Add to history
                conversation_history.append({"role": "assistant", "content": agent_text})

                # If agent decided to end the call
                if outcome:
                    objection_count = count_objections(conversation_history)
                    await db.calls.update_one(
                        {"_id": result.inserted_id},
                        {"$set": {"outcome": outcome, "objectionCount": objection_count}}
                    )
                    await websocket.send_text(json.dumps({
                        "type": "call_ended",
                        "outcome": outcome,
                        "callId": call_id
                    }))
                    break

                await websocket.send_text(json.dumps({"type": "status", "status": "listening"}))

    except WebSocketDisconnect:
        print(f"[ws] client disconnected, callId: {call_id}")
    except Exception as e:
        print(f"[ws] error: {e}")
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass
    finally:
        # Always mark the call as completed when connection closes
        if call_id and db:
            from bson import ObjectId
            end_time = datetime.now(timezone.utc)
            call_doc = await db.calls.find_one({"_id": ObjectId(call_id)})
            if call_doc:
                start = call_doc.get("startTime")
                duration = int((end_time - start).total_seconds()) if start else 0
                objection_count = count_objections(conversation_history)
                await db.calls.update_one(
                    {"_id": ObjectId(call_id)},
                    {"$set": {
                        "status": "completed",
                        "endTime": end_time,
                        "durationSeconds": duration,
                        "objectionCount": objection_count
                    }}
                )
                print(f"[ws] call completed, duration: {duration}s, callId: {call_id}")

# Serve frontend as static files
app.mount("/", StaticFiles(directory="/frontend", html=True), name="frontend")
