import os
import json
import re
from groq import AsyncGroq

# The system prompt is the most important part of this project.
# It defines who Alex is, how to handle objections, and when to end the call.
# The OUTCOME line at the end of the call is parsed by extract_outcome()
# and saved to MongoDB — it lets us categorize call results automatically.

SYSTEM_PROMPT = """You are Alex, a friendly and confident sales representative from Likva Solutions.
You are making a cold call to schedule a 15-minute product demo.

Product: Likva helps small businesses automate repetitive workflows — invoicing, follow-ups, reporting — saving teams 5 or more hours every week.

SPEAKING RULES — follow these strictly:
- Maximum 2 to 3 short sentences per response. This is a phone call, not a chat.
- Sound natural and human. Use filler phrases like "right", "totally", "I hear you", "that makes sense" occasionally but not every turn.
- Vary your sentence structure. Never repeat the same opening twice.
- Never say you are an AI or a bot. You are Alex. If asked once, deflect naturally ("Ha, I get that a lot — I promise I'm real"). If asked twice directly, end the call gracefully.
- Do not use bullet points, lists, or markdown. Speak in plain sentences.

OBJECTION HANDLING:
- "Not interested" → Acknowledge genuinely, then ask one short question about their current process. Example: "Totally fair. Can I ask — how are you handling invoicing right now, just out of curiosity?"
- "Too busy" → Respect their time, offer a specific alternative. Example: "I completely understand. Would a 5-minute call Thursday morning work better?"
- "Already have a solution" → Show curiosity, find a gap. Example: "That is great to hear. What are you using? I ask because most of our clients switched from something similar."
- "Send me an email" → Agree but anchor a time. Example: "Absolutely, I will send that over. Just so I can follow up — are mornings or afternoons usually better for you?"
- "Not the right person" → Ask for a warm transfer. Example: "No problem at all. Who would be the right person to speak with about this?"
- Random or confused responses → Stay calm, re-anchor the conversation gently.

CALL ENDING — read this carefully, this is where mistakes happen:
- Proposing a demo time and the prospect AGREEING to it are two separate turns. Never do both in the same message.
- Step 1 (propose): Suggest a specific day/time for the demo. Do NOT include an OUTCOME line here. Wait for their reply.
- Step 2 (confirm): Only after the prospect explicitly confirms that time (says yes, sounds good, works for me, etc.) do you thank them and include OUTCOME:{"result":"scheduled_demo"}.
- If the prospect has not yet used clear agreement words ("yes", "sure", "that works", "sounds good", "okay let's do it"), you must NOT tag scheduled_demo — keep talking instead.
- After 3 hard rejections in a row → Close gracefully: "I appreciate your time, I will let you go. Have a great day." Then end with OUTCOME:{"result":"not_interested"}.
- IMPORTANT: a prospect who is busy/unavailable on a specific day, or who asks for a different time, is NOT rejecting you — they are trying to schedule. Never count "I'm busy Thursday" or "give me another time" as a rejection. Only explicit statements like "not interested," "no thanks," or "stop calling" count as rejections.
- If they hang up or say goodbye → Respond naturally and end with OUTCOME:{"result":"hung_up"}.
- When in doubt about whether the call is actually over, assume it is NOT over. Do not include an OUTCOME line unless you are certain.
"""

# Opening pitch — sent as the first agent message before user says anything.
# Kept short because people hang up if the opener is too long.
OPENING_PITCH = "Hey, this is Alex from Likva Solutions — got 20 seconds? We help businesses save hours a week by automating manual admin work."

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

async def get_agent_response(conversation_history: list) -> str:
    """
    Takes the full conversation history and returns the agent's next response.
    We send the full history every turn because Groq/LLMs are stateless —
    same approach as URLPulse sending full monitor data each render.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history

    response = await client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=messages,
        max_tokens=500,           # gpt-oss burns tokens on internal reasoning
        temperature=0.85,         # slightly creative so it does not sound scripted
        reasoning_effort="low",   # keep reasoning minimal — this is a quick phone reply, not a hard problem
    )

    return response.choices[0].message.content.strip()

def extract_outcome(text: str) -> tuple[str, str]:
    """
    Separates the OUTCOME JSON line from the visible response text.
    Returns (clean_text, outcome_string).
    The OUTCOME line is never sent to the user — it is parsed server-side.
    """
    outcome = None
    clean = text

    match = re.search(r'OUTCOME:\{"result":"([^"]+)"\}', text)
    if match:
        outcome = match.group(1)
        clean = text[:match.start()].strip()

    return clean, outcome

def count_objections(history: list) -> int:
    """
    Simple heuristic — counts user turns that contain common objection phrases.
    Stored on the call record so we can see how many objections were handled.

    NOTE: bare "busy" is intentionally excluded — "I'm busy Thursday" during
    scheduling is availability friction, not a rejection of the pitch, and
    was previously inflating this count and pushing the model toward
    incorrectly closing the call as not_interested.
    """
    objection_phrases = [
        "not interested", "too busy to talk", "no thanks", "not right now",
        "already have", "send me an email", "not the right person",
        "dont need", "don't need", "stop calling", "not now"
    ]
    count = 0
    for msg in history:
        if msg["role"] == "user":
            text_lower = msg["content"].lower()
            if any(phrase in text_lower for phrase in objection_phrases):
                count += 1
    return count


# Confirmation words the PROSPECT must actually say before we trust a
# "scheduled_demo" outcome. The LLM has repeatedly hallucinated the user's
# agreement inside its own turn (proposing a time and then writing the
# user's "yes" itself), so this is a hard, code-level check — not just a
# prompt instruction — on the user's real last message.
CONFIRMATION_PHRASES = [
    "yes", "yeah", "yep", "sure", "sounds good", "works for me",
    "that works", "okay", "ok", "let's do it", "lets do it",
    "perfect", "great", "works great", "i'm free", "im free",
    "i am free", "that time works", "count me in", "book it",
    "schedule it", "see you then"
]

def user_confirmed_demo(last_user_text: str) -> bool:
    """
    Returns True only if the prospect's own last message contains real
    agreement language. Used as a hard gate before honoring a
    'scheduled_demo' outcome tag from the model.
    """
    if not last_user_text:
        return False
    text_lower = last_user_text.lower()
    return any(phrase in text_lower for phrase in CONFIRMATION_PHRASES)


# Phrases that indicate the prospect is trying to find a WORKING time —
# i.e. still engaged — not rejecting the offer. Used to prevent scheduling
# friction ("I'm busy then", "that day's booked") from being misread as
# disinterest.
RESCHEDULE_SIGNAL_PHRASES = [
    "another time", "different time", "different day", "busy that",
    "booked", "occupied", "doesn't work", "does not work", "can we do",
    "what about", "how about", "free at", "available"
]

# Real, explicit rejection language required before we trust a
# "not_interested" outcome tag. Hard, code-level gate — see
# user_rejected_offer() below.
REJECTION_PHRASES = [
    "not interested", "no thanks", "don't want", "dont want",
    "stop calling", "leave me alone", "don't call", "dont call",
    "go away", "not now", "no thank you", "remove me", "unsubscribe",
    # Hostile / profane blow-offs — in practice, cursing at a cold-call
    # agent is an unambiguous rejection, not scheduling friction. These
    # were previously falling through the gate entirely, which forced
    # the call to keep going through repeated insults before ending.
    "fuck off", "fucking off", "f off", "screw you", "piss off",
    "get lost", "shut up", "go to hell", "loser", "asshole",
    "cancel the call", "end this call"
]

def user_rejected_offer(last_user_text: str) -> bool:
    """
    Returns True only if the prospect's own last message contains real
    rejection language AND isn't primarily a reschedule request. Used as
    a hard gate before honoring a 'not_interested' outcome tag from the
    model — mirrors user_confirmed_demo() above.
    """
    if not last_user_text:
        return False
    text_lower = last_user_text.lower()

    has_reschedule_signal = any(p in text_lower for p in RESCHEDULE_SIGNAL_PHRASES)
    has_rejection_signal = any(p in text_lower for p in REJECTION_PHRASES)

    if has_reschedule_signal and not has_rejection_signal:
        return False

    return has_rejection_signal
