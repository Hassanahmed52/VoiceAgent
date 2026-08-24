import os
import json
import base64
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from starlette.staticfiles import StaticFiles as BaseStaticFiles
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

from db import connect_db, close_db, get_db
from agent import get_agent_response, extract_outcome, count_objections, user_confirmed_demo, user_rejected_offer, OPENING_PITCH
from speech import text_to_speech, speech_to_text

# Guard against the LLM ending the call too early. gpt-oss models tend to be
# "eager" about resolving open loops and will sometimes tag an OUTCOME after
# just one exchange even when nothing conclusive happened. We only honor an
# outcome once the prospect has actually spoken this many times — except
# "hung_up" and a clearly-confirmed "not_interested" rejection, which can
# legitimately happen early.
MIN_USER_TURNS_BEFORE_END = 3

@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    # Pre-warm TTS so first caller hears audio immediately
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, text_to_speech, OPENING_PITCH)
    print("[startup] TTS pre-warmed")
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

@app.delete("/api/calls/{call_id}")
async def delete_call(call_id: str):
    """Delete a single call from history."""
    from bson import ObjectId
    db = get_db()
    try:
        oid = ObjectId(call_id)
    except Exception:
        return JSONResponse(status_code=400, content={"message": "Invalid call ID"})

    result = await db.calls.delete_one({"_id": oid})
    if result.deleted_count == 0:
        return JSONResponse(status_code=404, content={"message": "Call not found"})
    return {"success": True, "data": {"deletedId": call_id}}

@app.delete("/api/calls")
async def clear_calls(callerId: str):
    """Clear all call history for a given caller (browser UUID)."""
    if not callerId:
        return JSONResponse(status_code=400, content={"message": "callerId required"})
    db = get_db()
    result = await db.calls.delete_many({"callerId": callerId})
    return {"success": True, "data": {"deletedCount": result.deleted_count}}

@app.websocket("/ws/call")
async def websocket_call(websocket: WebSocket):
    await websocket.accept()

    db = get_db()
    call_id = None
    call_object_id = None
    conversation_history = []
    caller_id = None
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

        # This is instant now because TTS was pre-warmed at startup
        audio_bytes = await asyncio.get_event_loop().run_in_executor(
            None, text_to_speech, OPENING_PITCH
        )

        await websocket.send_text(json.dumps({
            "type": "transcript", "role": "agent", "text": OPENING_PITCH
        }))

        if audio_bytes:
            await websocket.send_text(json.dumps({
                "type": "audio",
                "data": base64.b64encode(audio_bytes).decode()
            }))

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
                    await websocket.send_text(json.dumps({"type": "status", "status": "listening"}))
                    continue

                user_text = await speech_to_text(audio_data)
                print(f"[ws] STT: '{user_text}'")

                if not user_text or not user_text.strip():
                    retry = "Sorry, I didn't catch that. Could you say that again?"
                    await websocket.send_text(json.dumps({
                        "type": "transcript", "role": "agent", "text": retry
                    }))
                    retry_audio = await asyncio.get_event_loop().run_in_executor(
                        None, text_to_speech, retry
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
                    user_turns = sum(1 for m in conversation_history if m["role"] == "user")

                    # --- Step 1: content gates. Does the user's own last message
                    # actually support this outcome? Catches the model
                    # hallucinating agreement/rejection that was never said.
                    if outcome == "scheduled_demo" and not user_confirmed_demo(user_text):
                        print(f"[ws] ignoring scheduled_demo — user's last message has no confirmation: '{user_text}'")
                        outcome = None
                    elif outcome == "not_interested" and not user_rejected_offer(user_text):
                        print(f"[ws] ignoring not_interested — user's last message reads as reschedule, not rejection: '{user_text}'")
                        outcome = None

                    # --- Step 2: minimum-turns guard. Stops the model from
                    # closing the call absurdly early on flimsy grounds.
                    # Exempted for hung_up (a real goodbye can happen on turn
                    # one) and for not_interested when the user's own words
                    # already passed the explicit-rejection check above —
                    # forcing someone who clearly said "not interested, end
                    # the call" through more turns just to hit a quota isn't
                    # careful, it's just annoying.
                    if outcome and outcome not in ("hung_up",) and user_turns < MIN_USER_TURNS_BEFORE_END:
                        is_confirmed_rejection = outcome == "not_interested" and user_rejected_offer(user_text)
                        if not is_confirmed_rejection:
                            print(f"[ws] ignoring premature outcome '{outcome}' after {user_turns} user turn(s)")
                            outcome = None

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
        print(f"[ws] disconnected: {call_id}")
    except Exception as e:
        print(f"[ws] error: {e}")
        import traceback
        traceback.print_exc()
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass
    finally:
        if call_id is not None:
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
                print(f"[ws] call saved: {duration}s")
            except Exception as e:
                print(f"[ws] error saving: {e}")

class NoCacheStaticFiles(BaseStaticFiles):
    """
    Forces browsers to always revalidate static files (HTML/JS/CSS) with the
    server instead of silently serving a stale cached copy. Without this,
    FastAPI's default StaticFiles sends no Cache-Control header, so browsers
    fall back to their own heuristic caching — which is why editing
    call.v2.js in place worked in incognito (no cache) but not in a normal
    browser (stale cache hit). "no-cache" here does NOT mean "never cache" —
    it means "always ask the server first," so the browser still gets fast
    304 Not Modified responses when nothing changed, just never a blind
    stale hit.
    """
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if hasattr(response, "headers"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.mount("/", NoCacheStaticFiles(directory="/frontend", html=True), name="frontend")
