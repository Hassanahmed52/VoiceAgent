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

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/calls")
async def get_calls(callerId: str):
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

@app.websocket("/ws/call")
async def websocket_call(websocket: WebSocket):
    await websocket.accept()

    db = get_db()
    call_id = None
    call_object_id = None
    conversation_history = []
    caller_id = None
    # Use naive UTC throughout to match what MongoDB returns
    start_time = datetime.utcnow()

    try:
        raw = await websocket.receive_text()
        msg = json.loads(raw)

        if msg.get("type") != "start":
            await websocket.send_text(json.dumps({"type": "error", "message": "Expected start message"}))
            return

        caller_id = msg.get("callerId", "anonymous")

        call_doc = {
            "callerId": caller_id,
            "startTime": start_time,
            "status": "active",
            "transcript": [],
            "objectionCount": 0,
            "outcome": None
        }
        result = await db.calls.insert_one(call_doc)
        call_object_id = result.inserted_id
        call_id = str(call_object_id)
        print(f"[ws] call started: {call_id}")

        await websocket.send_text(json.dumps({"type": "status", "status": "speaking"}))

        audio_bytes = await asyncio.get_event_loop().run_in_executor(
            None, text_to_speech, OPENING_PITCH
        )

        await websocket.send_text(json.dumps({
            "type": "transcript",
            "role": "agent",
            "text": OPENING_PITCH
        }))

        if audio_bytes:
            print(f"[ws] sending opening audio: {len(audio_bytes)} bytes")
            await websocket.send_text(json.dumps({
                "type": "audio",
                "data": base64.b64encode(audio_bytes).decode()
            }))
        else:
            print("[ws] warning: no audio for opening pitch")

        await db.calls.update_one(
            {"_id": call_object_id},
            {"$push": {"transcript": {
                "role": "agent",
                "text": OPENING_PITCH,
                "timestamp": datetime.utcnow().isoformat()
            }}}
        )

        conversation_history.append({"role": "assistant", "content": OPENING_PITCH})
        await websocket.send_text(json.dumps({"type": "status", "status": "listening"}))

        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg.get("type") == "end":
                print("[ws] user ended call")
                break

            if msg.get("type") == "audio":
                await websocket.send_text(json.dumps({"type": "status", "status": "processing"}))

                audio_data = base64.b64decode(msg["data"])
                print(f"[ws] received audio: {len(audio_data)} bytes")

                if len(audio_data) < 500:
                    print("[ws] audio too small, skipping")
                    await websocket.send_text(json.dumps({"type": "status", "status": "listening"}))
                    continue

                user_text = await speech_to_text(audio_data)
                print(f"[ws] STT result: '{user_text}'")

                if not user_text or not user_text.strip():
                    retry_msg = "Sorry, I didn't catch that. Could you try again?"
                    await websocket.send_text(json.dumps({
                        "type": "transcript", "role": "agent", "text": retry_msg
                    }))
                    retry_audio = await asyncio.get_event_loop().run_in_executor(
                        None, text_to_speech, retry_msg
                    )
                    if retry_audio:
                        await websocket.send_text(json.dumps({
                            "type": "audio",
                            "data": base64.b64encode(retry_audio).decode()
                        }))
                    await websocket.send_text(json.dumps({"type": "status", "status": "listening"}))
                    continue

                await websocket.send_text(json.dumps({
                    "type": "transcript", "role": "user", "text": user_text
                }))

                await db.calls.update_one(
                    {"_id": call_object_id},
                    {"$push": {"transcript": {
                        "role": "user",
                        "text": user_text,
                        "timestamp": datetime.utcnow().isoformat()
                    }}}
                )

                conversation_history.append({"role": "user", "content": user_text})

                await websocket.send_text(json.dumps({"type": "status", "status": "speaking"}))
                agent_text_raw = await get_agent_response(conversation_history)
                agent_text, outcome = extract_outcome(agent_text_raw)
                print(f"[ws] agent: '{agent_text}' outcome: {outcome}")

                await websocket.send_text(json.dumps({
                    "type": "transcript", "role": "agent", "text": agent_text
                }))

                audio_bytes = await asyncio.get_event_loop().run_in_executor(
                    None, text_to_speech, agent_text
                )
                if audio_bytes:
                    await websocket.send_text(json.dumps({
                        "type": "audio",
                        "data": base64.b64encode(audio_bytes).decode()
                    }))

                await db.calls.update_one(
                    {"_id": call_object_id},
                    {"$push": {"transcript": {
                        "role": "agent",
                        "text": agent_text,
                        "timestamp": datetime.utcnow().isoformat()
                    }}}
                )

                conversation_history.append({"role": "assistant", "content": agent_text})

                if outcome:
                    objection_count = count_objections(conversation_history)
                    await db.calls.update_one(
                        {"_id": call_object_id},
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
        print(f"[ws] client disconnected: {call_id}")
    except Exception as e:
        print(f"[ws] error: {e}")
        import traceback
        traceback.print_exc()
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass
    finally:
        if call_id is not None and db is not None:
            from bson import ObjectId
            end_time = datetime.utcnow()
            try:
                duration = int((end_time - start_time).total_seconds())
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
                print(f"[ws] call saved: {duration}s, objections: {objection_count}")
            except Exception as e:
                print(f"[ws] error saving call: {e}")
                import traceback
                traceback.print_exc()

app.mount("/", StaticFiles(directory="/frontend", html=True), name="frontend")
