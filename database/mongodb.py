import os
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "chill_out")

client: AsyncIOMotorClient = None
db = None


async def connect_to_mongo():
    """Initialize the MongoDB connection pool."""
    global client, db
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[MONGO_DB_NAME]
    print("MongoDB Connected ✅")

    # Ensure indexes for performance
    await db.users.create_index("mobile_no", unique=True)
    await db.rooms.create_index("room_url", unique=True)
    # Drop the old non-unique index if it exists, then recreate as unique
    try:
        await db.connected_users.drop_index("room_id_1_user_id_1")
    except Exception:
        pass  # Index doesn't exist yet — that's fine
    await db.connected_users.create_index(
        [("room_id", 1), ("user_id", 1)],
        unique=True,
        background=True
    )
    await db.uploaded_songs.create_index("song_name")
    await db.chat_records.create_index("room_id")
    await db.room_destination.create_index("room_id", unique=True)
    await db.temp_room_song.create_index("room_id")
    await db.chat_messages.create_index("room_id")
    await db.chat_messages.create_index("timestamp_ms")

    # Create a counter collection for auto-incrementing IDs
    # (mirrors MySQL auto-increment behavior)
    counters = db.counters
    for collection_name in [
        "users", "rooms", "connected_users", "uploaded_songs",
        "chat_records", "room_destination", "temp_room_song", "room_song",
        "chat_messages"
    ]:
        existing = await counters.find_one({"_id": collection_name})
        if not existing:
            await counters.insert_one({"_id": collection_name, "seq": 0})


async def close_mongo_connection():
    """Close the MongoDB connection."""
    global client
    if client:
        client.close()
        print("MongoDB Disconnected")


async def get_next_sequence(collection_name: str) -> int:
    """Get the next auto-increment ID for a collection."""
    result = await db.counters.find_one_and_update(
        {"_id": collection_name},
        {"$inc": {"seq": 1}},
        return_document=True
    )
    return result["seq"]


def get_db():
    """Return the database instance."""
    return db
