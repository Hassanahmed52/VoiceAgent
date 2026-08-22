import os
from motor.motor_asyncio import AsyncIOMotorClient

# Motor is the async MongoDB driver for Python.
# We use a module-level client so the connection pool is shared
# across all requests — same pattern as mongoose in URLPulse.

_client = None
_db = None

async def connect_db():
    global _client, _db
    uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("DB_NAME", "voiceagent")
    _client = AsyncIOMotorClient(uri)
    _db = _client[db_name]
    print(f"[db] connected to MongoDB: {db_name}")

async def close_db():
    global _client
    if _client:
        _client.close()

def get_db():
    return _db
