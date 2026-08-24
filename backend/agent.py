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

CALL ENDING:
- If the prospect agrees to a demo → Confirm a time, thank them warmly, end the call.
- After 3 hard rejections in a row → Close gracefully: "I appreciate your time, I will let you go. Have a great day." Then end.
- If they hang up or say goodbye → Respond naturally and end.

CRITICAL — at the very end of your final message when the call is over, append exactly this on a new line, nothing else:
OUTCOME:{"result":"scheduled_demo"}
or
OUTCOME:{"result":"callback_requested"}
or
OUTCOME:{"result":"not_interested"}
or
OUTCOME:{"result":"hung_up"}

Choose whichever fits. Do not include the OUTCOME line in any message except the final one.
"""

# Opening pitch — sent as the first agent message before user says anything.
# Kept short because people hang up if the opener is too long.
OPENING_PITCH = "Hi, quick sec — this is Alex from Likva Solutions. We help small businesses save hours by automating repetitive work. Got 30 seconds?"

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
    """
    objection_phrases = [
        "not interested", "too busy", "no thanks", "not right now",
        "already have", "send me an email", "not the right person",
        "dont need", "don't need", "call back", "busy"
    ]
    count = 0
    for msg in history:
        if msg["role"] == "user":
            text_lower = msg["content"].lower()
            if any(phrase in text_lower for phrase in objection_phrases):
                count += 1
    return count
