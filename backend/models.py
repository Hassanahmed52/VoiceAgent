from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# TranscriptEntry — one turn in the conversation
class TranscriptEntry(BaseModel):
    role: str          # "agent" or "user"
    text: str
    timestamp: datetime

# CallRecord — what we store in MongoDB per call
class CallRecord(BaseModel):
    callerId: str      # UUID from browser localStorage
    startTime: datetime
    endTime: Optional[datetime] = None
    durationSeconds: Optional[int] = None
    status: str = "active"   # "active" | "completed"
    transcript: List[dict] = []
    objectionCount: int = 0
    outcome: Optional[str] = None
    # outcome values: scheduled_demo | callback_requested | not_interested | hung_up
