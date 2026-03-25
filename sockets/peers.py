import socketio
import traceback
from urllib.parse import parse_qs
from database.mongodb import get_db, get_next_sequence
from datetime import datetime, timezone
from services.notification_service import send_notification_for_offline_users

# Create a python-socketio AsyncServer
# The path is configured on ASGIApp in main.py, not here
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
)

connected_devices = 0
room_seek_position = {}
room_queue_list = {}

# We use a namespace to match the Node.js "/webrtcPeer" namespace
NAMESPACE = "/webrtcPeer"


# ── Helpers ────────────────────────────────────────────────────────


def _extract_user_id_from_environ(environ: dict) -> str | None:
    """
    Extract userId from the ASGI/WSGI environ dict.

    python-engineio passes environ in different formats depending on the
    version and transport.  We try every known layout so the extraction
    never silently returns None.

    Possible layouts:
      1) WSGI-style:  environ["QUERY_STRING"] = "userId=113&..."   (str)
      2) Raw ASGI scope:  environ["query_string"] = b"userId=113&..."  (bytes)
      3) Nested scope:  environ["asgi.scope"]["query_string"] = b"..."  (bytes)
    """
    query_string = ""

    # Method 1 – WSGI-style (string, already decoded)
    if environ.get("QUERY_STRING"):
        query_string = environ["QUERY_STRING"]
    # Method 2 – raw ASGI scope key (bytes)
    elif environ.get("query_string"):
        qs = environ["query_string"]
        query_string = qs.decode("utf-8") if isinstance(qs, bytes) else str(qs)
    # Method 3 – nested inside asgi.scope
    elif isinstance(environ.get("asgi.scope"), dict):
        qs = environ["asgi.scope"].get("query_string", b"")
        query_string = qs.decode("utf-8") if isinstance(qs, bytes) else str(qs)

    if not query_string:
        return None

    parsed = parse_qs(query_string)
    user_id_list = parsed.get("userId")
    if user_id_list:
        return user_id_list[0]
    return None


def _room_key(room_id) -> str:
    """
    Normalise room_id to a string for use as a python-socketio room name.

    Node.js Socket.IO internally converts room names to strings.
    python-socketio does NOT – so room 5 (int) and room "5" (str)
    are treated as different rooms.  Always using str() prevents this
    mismatch.
    """
    return str(room_id)


def _safe_int(value, default=None):
    """Safely convert a value to int. Returns default if conversion fails."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# ── Connection lifecycle ───────────────────────────────────────────


@sio.on("connect", namespace=NAMESPACE)
async def on_connect(sid, environ, auth=None):
    global connected_devices
    connected_devices += 1

    user_id = _extract_user_id_from_environ(environ)
    print(f"peers connected")
    print(f"connected devices :{connected_devices}")

    if not user_id:
        print(f"[CONNECT] WARNING: userId is None! environ keys={list(environ.keys()) if isinstance(environ, dict) else type(environ)}")

    # Store userId in session — same as socket.handshake.query.userId in Node.js
    async with sio.session(sid, namespace=NAMESPACE) as session:
        session["userId"] = user_id

    await sio.emit("connection-success", {"success": sid}, room=sid, namespace=NAMESPACE)

    # Node.js broadcasts "joined-peers" to every client on each new connection
    await sio.emit("joined-peers", {}, namespace=NAMESPACE)


@sio.on("disconnect", namespace=NAMESPACE)
async def on_disconnect(sid):
    global connected_devices
    connected_devices -= 1

    session = await sio.get_session(sid, namespace=NAMESPACE)
    user_id = session.get("userId") if session else None

    print(f"peers disconnected")
    print(f"connected devices :{connected_devices}")

    if not user_id:
        return

    try:
        db = get_db()
        int_user_id = _safe_int(user_id)
        if int_user_id is None:
            return

        # Matches Node.js: UPDATE connected_users SET is_online = 0 WHERE user_id = ?
        # Blanket update by user_id — same as Node.js behavior
        result = await db.connected_users.update_many(
            {"user_id": int_user_id},
            {"$set": {
                "is_online": 0,
                "is_mute": 0,
                "latitude": "",
                "longitude": "",
            }}
        )
        if result.matched_count == 0:
            print(f"There is No data to the userId {user_id}")

        # Clear speaker status if this user was speaking
        speaker_cursor = db.rooms.find(
            {"speaker": int_user_id},
            {"_id": 0, "room_id": 1}
        )
        speaker_rooms = await speaker_cursor.to_list(None)
        if speaker_rooms:
            await db.rooms.update_one(
                {"room_id": speaker_rooms[0]["room_id"]},
                {"$set": {"speaker": 0}}
            )

        # If user is admin of an active room, delete queue and temp_room_song
        admin_cursor = db.rooms.find(
            {"created_by": int_user_id, "is_active": 1},
            {"_id": 0, "room_id": 1}
        )
        admin_rooms = await admin_cursor.to_list(None)
        if admin_rooms:
            room_queue_list.pop(admin_rooms[0]["room_id"], None)
            await db.temp_room_song.delete_many({"room_id": admin_rooms[0]["room_id"]})

        # Broadcast peer-disconnected
        await _disconnected_peer(sid, user_id)

    except Exception as error:
        print(f"There is an error while updating offline status to the userId {user_id}: {error}")
        traceback.print_exc()


async def _disconnected_peer(socket_id, user_id):
    """Broadcast peer-disconnected event with username."""
    try:
        db = get_db()
        user = await db.users.find_one({"id": _safe_int(user_id)}, {"_id": 0, "username": 1})
        username = user.get("username", "") if user else ""
    except Exception as error:
        print(f"Error in disconnectedPeer for userId {user_id}: {error}")
        username = ""

    await sio.emit("peer-disconnected", {
        "socketID": socket_id,
        "userId": user_id,
        "userName": username,
    }, namespace=NAMESPACE)


# ── Room join / leave ──────────────────────────────────────────────


@sio.on("onlinePeers", namespace=NAMESPACE)
async def on_online_peers(sid, data):
    """Establish connection when entering a room — matches Node.js onlinePeers exactly."""
    session = await sio.get_session(sid, namespace=NAMESPACE)
    user_id = session.get("userId") if session else None

    if not user_id:
        print(f"[onlinePeers] SKIP: no userId in session for sid={sid}")
        return

    try:
        room_id = data.get("roomId")
        socket_id = data.get("socketID")
        int_user_id = _safe_int(user_id)
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)

        if int_user_id is None or int_room_id is None:
            print(f"[onlinePeers] SKIP: bad userId={user_id} or roomId={room_id}")
            return

        print(f"{int_user_id} User Joined Room {room_id}")

        # Join the socket.io room — same as socket.join(data.roomId) in Node.js
        await sio.enter_room(sid, room, namespace=NAMESPACE)
        db = get_db()

        # UPDATE connected_users SET is_online = 1, socket_id = ? WHERE room_id = ? AND user_id = ?
        result = await db.connected_users.update_one(
            {"room_id": int_room_id, "user_id": int_user_id},
            {"$set": {
                "is_online": 1,
                "socket_id": sid,
            }}
        )

        if result.matched_count == 0:
            print(f"There is No data to the userId {user_id}")
            return

        # Fetch ALL online users in this room
        # SELECT user_id AS userId, is_mute AS isMute, CONCAT(latitude,',',longitude) AS latLng
        # FROM connected_users WHERE room_id = ? AND is_online = 1
        online_users_cursor = db.connected_users.find(
            {"room_id": int_room_id, "is_online": 1},
            {"_id": 0, "user_id": 1, "is_mute": 1, "latitude": 1, "longitude": 1}
        )
        online_users = await online_users_cursor.to_list(None)

        if not online_users:
            print(f"There is an No existing users id in the room {room_id}")
            return

        # Build users dict: { "userId": { isMute, latLng } }
        # Matches Node.js: fetchUserResponse.data.reduce((acc, user) => { acc[user.userId] = ... })
        users = {}
        for u in online_users:
            lat = u.get("latitude", "") or ""
            lng = u.get("longitude", "") or ""
            lat_lng = f"{lat},{lng}"
            users[str(u["user_id"])] = {
                "isMute": u.get("is_mute", 0),
                "latLng": lat_lng
            }

        # peers.to(data.roomId).emit("existing-peer", users)
        await sio.emit("existing-peer", users, room=room, namespace=NAMESPACE)

        # Send current speaker info
        room_doc = await db.rooms.find_one(
            {"room_id": int_room_id},
            {"_id": 0, "speaker": 1}
        )
        if room_doc and room_doc.get("speaker"):
            await sio.emit("speaking", {
                "userId": room_doc["speaker"],
                "isSpeaking": True,
            }, room=room, namespace=NAMESPACE)

        # Activate room if this user is the creator
        await db.rooms.update_one(
            {"room_id": int_room_id, "created_by": int_user_id},
            {"$set": {"is_active": 1}}
        )

        # Get current song for room
        temp_song = await db.temp_room_song.find_one(
            {"room_id": int_room_id},
            {"_id": 0}
        )

        # peers.to(data.roomId).emit("online-peer", { ... })
        await sio.emit("online-peer", {
            "socketID": socket_id,
            "userId": int_user_id,
            "songUrl": temp_song.get("song", "") if temp_song else "",
            "songTitle": temp_song.get("song_title", "") if temp_song else "",
            "artist": temp_song.get("artist", "") if temp_song else "",
            "thumbnailUrl": temp_song.get("thumbnail_url", "") if temp_song else "",
            "seekPosition": room_seek_position.get(int_room_id, temp_song.get("seek_position", "") if temp_song else ""),
            "isPlaying": (temp_song.get("is_playing", 0) if temp_song else 0) == 1,
            "queue": room_queue_list.get(int_room_id)
        }, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"There is an error while updating online room status to the userId {user_id}")
        print(f"Error: {error}")
        traceback.print_exc()


@sio.on("offlinePeers", namespace=NAMESPACE)
async def on_offline_peers(sid, data):
    """Disconnect when leaving a room — matches Node.js offlinePeers exactly."""
    session = await sio.get_session(sid, namespace=NAMESPACE)
    user_id = session.get("userId") if session else None

    if not user_id:
        return

    try:
        room_id = data.get("roomId")
        socket_id = data.get("socketID")
        int_user_id = _safe_int(user_id)
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)

        if int_user_id is None or int_room_id is None:
            return

        print(f"{int_user_id} User Left Room {room_id}")

        # Leave the socket.io room — same as socket.leave(data.roomId) in Node.js
        # Node.js calls leave BEFORE the DB update
        await sio.enter_room(sid, room, namespace=NAMESPACE)
        db = get_db()

        # UPDATE connected_users SET is_online = 0, is_mute = 0, latitude='', longitude=''
        # WHERE room_id = ? AND user_id = ?
        result = await db.connected_users.update_one(
            {"room_id": int_room_id, "user_id": int_user_id},
            {"$set": {
                "is_online": 0,
                "is_mute": 0,
                "latitude": "",
                "longitude": "",
            }}
        )

        # Clear speaker if this user was speaking
        room_doc = await db.rooms.find_one(
            {"room_id": int_room_id},
            {"_id": 0, "speaker": 1, "created_by": 1}
        )
        if room_doc and room_doc.get("speaker") == int_user_id:
            await db.rooms.update_one(
                {"room_id": int_room_id},
                {"$set": {"speaker": 0}}
            )

        # If admin leaves, delete queue and temp_room_song
        if room_doc and room_doc.get("created_by") == int_user_id:
            room_queue_list.pop(int_room_id, None)
            await db.temp_room_song.delete_many({"room_id": int_room_id})

        if result.matched_count == 0:
            print(f"There is No data to the userId {user_id}")
        else:
            # peers.to(data.roomId).emit("offline-peer", { ... })
            await sio.emit("offline-peer", {
                "socketID": socket_id,
                "userId": int_user_id,
            }, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"There is an error while updating offline status to the userId {user_id}")
        print(f"There is an error while updating offline status error: {error}")
        traceback.print_exc()


# ── WebRTC signalling ──────────────────────────────────────────────


@sio.on("host_lan_ip", namespace=NAMESPACE)
async def on_host_lan_ip(sid, data):
    """Relay host WiFi IP to all peers in the room for LAN fallback."""
    room_id = data.get("roomId")
    if room_id:
        room = _room_key(room_id)
        await sio.emit("host_lan_ip", {"ip": data.get("ip")}, room=room, skip_sid=sid, namespace=NAMESPACE)


@sio.on("candidate", namespace=NAMESPACE)
async def on_candidate(sid, data):
    """Send ICE candidate data to room."""
    room_id = data.get("roomId")
    room = _room_key(room_id)
    await sio.emit("candidate", {
        "candidate": data.get("payload"),
        "socketID": data.get("socketID", {}).get("local"),
    }, room=room, skip_sid=sid, namespace=NAMESPACE)


@sio.on("speaking", namespace=NAMESPACE)
async def on_speaking(sid, data):
    """Broadcast speaking status."""
    session = await sio.get_session(sid, namespace=NAMESPACE)
    user_id = session.get("userId") if session else None

    room_id = data.get("roomId")
    int_room_id = _safe_int(room_id)
    room = _room_key(room_id)
    is_speaking = data.get("isSpeaking", False)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    print(f"speaking-------------------> {is_speaking}")

    try:
        db = get_db()
        await db.rooms.update_one(
            {"room_id": int_room_id},
            {"$set": {
                "speaker": data.get("userId") if is_speaking else 0,
                "last_spoken": now
            }}
        )

        # socket.broadcast.in(data.roomId).emit("speaking", data)
        await sio.emit("speaking", data, room=room, skip_sid=sid, namespace=NAMESPACE)

        if is_speaking:
            await send_notification_for_offline_users(
                int_room_id,
                f"{data.get('userName', '')} is speaking",
                "call"
            )
    except Exception as error:
        print(f"There is an error while updating speaker {user_id}")


@sio.on("offer", namespace=NAMESPACE)
async def on_offer(sid, data):
    """Relay SDP offer to specific peer."""
    session = await sio.get_session(sid, namespace=NAMESPACE)
    user_id = session.get("userId") if session else None

    remote_sid = data.get("socketID", {}).get("remote")
    local_sid = data.get("socketID", {}).get("local")

    if remote_sid:
        await sio.emit("offer", {
            "sdp": data.get("payload", {}).get("sdp"),
            "socketID": local_sid,
            "userId": _safe_int(user_id),
        }, room=remote_sid, namespace=NAMESPACE)


@sio.on("answer", namespace=NAMESPACE)
async def on_answer(sid, data):
    """Relay SDP answer to specific peer."""
    remote_sid = data.get("socketID", {}).get("remote")
    local_sid = data.get("socketID", {}).get("local")

    if remote_sid:
        await sio.emit("answer", {
            "sdp": data.get("payload", {}).get("sdp"),
            "socketID": local_sid,
        }, room=remote_sid, namespace=NAMESPACE)


@sio.on("mute", namespace=NAMESPACE)
async def on_mute(sid, data):
    """Update mute status and broadcast."""
    try:
        user_id_data = data.get("userId")
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)
        is_mute = data.get("isMute", False)

        db = get_db()

        # Fetch socket_id
        conn_user = await db.connected_users.find_one(
            {"is_online": 1, "user_id": _safe_int(user_id_data), "room_id": int_room_id},
            {"_id": 0, "socket_id": 1}
        )

        if not conn_user:
            print(f"There is No socket Id to the user {user_id_data}")
            return

        # Update mute status
        await db.connected_users.update_one(
            {"room_id": int_room_id, "user_id": _safe_int(user_id_data)},
            {"$set": {"is_mute": 1 if is_mute else 0}}
        )

        # peers.to(data?.roomId).emit("muted", { ... })
        await sio.emit("muted", {
            "userId": _safe_int(user_id_data),
            "socketID": conn_user["socket_id"],
            "isMute": is_mute,
        }, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"There is an Error occuring while fetching socket Id to the user {data.get('userId')}")


@sio.on("hostTransfer", namespace=NAMESPACE)
async def on_host_transfer(sid, data):
    """Transfer host role to another user."""
    try:
        new_host_id = data.get("newHostId")
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)
        print(f"hostTransfer: roomId={room_id} newHostId={new_host_id}")

        if not room_id or not new_host_id:
            print("hostTransfer: missing roomId or newHostId")
            return

        db = get_db()
        await db.rooms.update_one(
            {"room_id": int_room_id},
            {"$set": {"created_by": new_host_id}}
        )

        await sio.emit("host-transfer", {
            "newHostId": new_host_id,
        }, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"Error in hostTransfer: {error}")


# ── Location ───────────────────────────────────────────────────────


@sio.on("livelocation", namespace=NAMESPACE)
async def on_live_location(sid, data):
    """Share live location coordinates."""
    session = await sio.get_session(sid, namespace=NAMESPACE)
    user_id = session.get("userId") if session else None

    if not user_id:
        return

    try:
        room_id = data.get("roomId")
        int_user_id = _safe_int(user_id)
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)
        lat = data.get("latLng", {}).get("lat")
        lng = data.get("latLng", {}).get("lng")

        # socket.broadcast.to(data?.roomId).emit("live-location", { ... })
        await sio.emit("live-location", {
            "lat": lat,
            "long": lng,
            "userId": int_user_id,
        }, room=room, skip_sid=sid, namespace=NAMESPACE)

        db = get_db()

        # Update lat/lng in connected_users
        result = await db.connected_users.update_one(
            {"room_id": int_room_id, "user_id": int_user_id},
            {"$set": {"latitude": lat, "longitude": lng}}
        )

        if result.matched_count == 0:
            print(f"There is No data for updating latitude and longitude to the userId {user_id}")
            return

        # Fetch other online users' locations
        other_users = await db.connected_users.find(
            {
                "room_id": int_room_id,
                "is_online": 1,
                "user_id": {"$ne": int_user_id},
                "latitude": {"$nin": [None, ""]},
                "longitude": {"$nin": [None, ""]},
            },
            {"_id": 0, "user_id": 1, "latitude": 1, "longitude": 1}
        ).to_list(None)

        if not other_users:
            print(f"There is No data while Fetching latitude and longitude from the roomId {room_id}")
        else:
            for ele in other_users:
                lat_lon = {
                    "lat": float(ele["latitude"]) if ele.get("latitude") else 0,
                    "long": float(ele["longitude"]) if ele.get("longitude") else 0,
                    "userId": ele["user_id"],
                }
                # socket.emit — send only to the requesting socket
                await sio.emit("live-location", lat_lon, room=sid, namespace=NAMESPACE)

        # Check room destination
        room_dest = await db.room_destination.find_one(
            {"room_id": int_room_id},
            {"_id": 0, "latitude": 1, "longitude": 1}
        )
        if room_dest:
            # socket.to(data?.roomId).emit("group-destination", { ... })
            await sio.emit("group-destination", {
                "lat": float(room_dest["latitude"]) if room_dest.get("latitude") else 0,
                "long": float(room_dest["longitude"]) if room_dest.get("longitude") else 0,
            }, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"There is an Error while updating latitude and longitude to the userId {user_id}")
        print(error)


# ── Audio / Music ──────────────────────────────────────────────────


@sio.on("eventStart", namespace=NAMESPACE)
async def on_event_start(sid, data):
    """Notify song playback start."""
    try:
        room_id = data.get("roomId")
        room = _room_key(room_id)
        print(f"eventStart roomId {room_id}")
        print(f"eventStart songTitle {data.get('songTitle')}")
        print(f"eventStart artist {data.get('artist')}")

        await sio.emit("event-Start", {
            "bytes": data.get("song"),
            "songTitle": data.get("songTitle"),
            "artist": data.get("artist"),
            "chunkCount": data.get("chunkCount"),
        }, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"Error in eventStart: {error}")


@sio.on("eventEnd", namespace=NAMESPACE)
async def on_event_end(sid, data):
    """Notify song playback end, save to DB."""
    try:
        room_id = data.get("roomId")
        room = _room_key(room_id)
        print(f"eventEnd roomId {room_id}")

        if room_id and data.get("userId"):
            await _add_temp_song(data)

        print(f"eventEnd songUrl {data.get('songUrl')}")

        await sio.emit("event-End", {
            "userId": data.get("userId"),
            "songTitle": data.get("songTitle"),
            "artist": data.get("artist"),
            "songUrl": data.get("songUrl"),
        }, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"Error in eventEnd: {error}")


@sio.on("destination", namespace=NAMESPACE)
async def on_destination(sid, data):
    """Set group destination."""
    try:
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)
        lat = data.get("latlon", {}).get("lat")
        lng = data.get("latlon", {}).get("long")
        print(f"destination {data}")

        db = get_db()
        existing = await db.room_destination.find_one({"room_id": int_room_id})

        if existing:
            await db.room_destination.update_one(
                {"room_id": int_room_id},
                {"$set": {"latitude": lat, "longitude": lng}}
            )
        else:
            new_id = await get_next_sequence("room_destination")
            await db.room_destination.insert_one({
                "id": new_id,
                "room_id": int_room_id,
                "latitude": lat,
                "longitude": lng
            })

        await sio.emit("group-destination", {
            "lat": lat,
            "long": lng,
        }, room=room, namespace=NAMESPACE)

        print(f"group-destination {{'lat': {lat}, 'long': {lng}}}")

    except Exception as error:
        print(error)


@sio.on("pushSongUrl", namespace=NAMESPACE)
async def on_push_song_url(sid, data):
    """Broadcast song to play."""
    try:
        room_id = data.get("roomId")
        room = _room_key(room_id)
        print(f"sendSongUrl data: {{roomId: {room_id}, userId: {data.get('userId')}, songUrl: {data.get('songUrl')}}}")

        if room_id and data.get("userId"):
            await _add_temp_song(data)

        await sio.emit("push-song-url", {
            "songUrl": data.get("songUrl"),
            "songTitle": data.get("songTitle"),
            "artist": data.get("artist"),
            "thumbnailUrl": data.get("thumbnailUrl"),
            "isPlaying": True
        }, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"Error in pushSongUrl: {error}")


@sio.on("playPause", namespace=NAMESPACE)
async def on_play_pause(sid, data):
    """Toggle play/pause."""
    try:
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)
        db = get_db()

        temp_song = await db.temp_room_song.find_one(
            {"room_id": int_room_id},
            {"_id": 0, "is_playing": 1}
        )

        playing_status = temp_song.get("is_playing", 0) if temp_song else 0
        pause = 0 if playing_status == 1 else 1

        await db.temp_room_song.update_one(
            {"room_id": int_room_id},
            {"$set": {"is_playing": pause}}
        )

        await sio.emit("play-pause", {
            "pause": pause == 0
        }, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"Error in playPause: {error}")


@sio.on("onSeek", namespace=NAMESPACE)
async def on_seek(sid, data):
    """Relay seek position."""
    try:
        room_id = data.get("roomId")
        room = _room_key(room_id)
        await sio.emit("seek-listen", data, room=room, namespace=NAMESPACE)
    except Exception as error:
        print(f"Error in onSeek: {error}")


@sio.on("onSeekUpdate", namespace=NAMESPACE)
async def on_seek_update(sid, data):
    """Store seek position in memory."""
    try:
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)
        seek = data.get("seek", "")
        print(f"Room Seek updated roomId: {room_id} seekPosition: {seek}")
        room_seek_position[int_room_id] = seek
    except Exception as error:
        print(f"Error in onSeekUpdate: {error}")


# ── Collaborative Queue Events ─────────────────────────────────────

@sio.on("updateQueue", namespace=NAMESPACE)
async def on_update_queue(sid, data):
    """Any user can add a song to the room queue."""
    try:
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)
        song_url = data.get("songUrl")
        if not room_id or not song_url:
            return

        if int_room_id not in room_queue_list:
            room_queue_list[int_room_id] = []

        # Prevent duplicate songs
        already_exists = any(
            item.get("songUrl") == song_url for item in room_queue_list[int_room_id]
        )
        if already_exists:
            print(f"Duplicate song skipped for roomId: {room_id} songUrl: {song_url}")
            return

        print(f"Song added in Queue on roomId: {room_id} songUrl: {song_url}")
        room_queue_list[int_room_id].append(data)

        await sio.emit("update-queue", data, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"Error in updateQueue: {error}")


@sio.on("reorderQueue", namespace=NAMESPACE)
async def on_reorder_queue(sid, data):
    """Any user can reorder the queue."""
    try:
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)
        queue = data.get("queue")
        if not room_id or not isinstance(queue, list):
            return

        room_queue_list[int_room_id] = queue
        print(f"Queue reordered for roomId: {room_id}, length: {len(queue)}")

        await sio.emit("reorder-queue", {"queue": queue}, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"Error in reorderQueue: {error}")


@sio.on("requestQueue", namespace=NAMESPACE)
async def on_request_queue(sid, data):
    """Late joiner requests current queue state."""
    try:
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)
        if not room_id:
            return

        queue = room_queue_list.get(int_room_id, [])
        print(f"Queue requested for roomId: {room_id}, length: {len(queue)}")
        await sio.emit("queue-state", {"queue": queue}, room=sid, namespace=NAMESPACE)

    except Exception as error:
        print(f"Error in requestQueue: {error}")


@sio.on("removeFromQueue", namespace=NAMESPACE)
async def on_remove_from_queue(sid, data):
    """Admin can remove a song from the queue."""
    try:
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)
        song_url = data.get("songUrl")
        if not room_id or not song_url:
            return

        if int_room_id not in room_queue_list:
            room_queue_list[int_room_id] = []

        room_queue_list[int_room_id] = [
            item for item in room_queue_list[int_room_id]
            if item.get("songUrl") != song_url
        ]

        print(f"Song removed from queue: roomId={room_id} songUrl={song_url}")
        await sio.emit("song-removed-from-queue", {"songUrl": song_url}, room=room, namespace=NAMESPACE)

    except Exception as error:
        print(f"Error in removeFromQueue: {error}")


@sio.on("setCurrentSong", namespace=NAMESPACE)
async def on_set_current_song(sid, data):
    """Set current playing song."""
    try:
        room_id = data.get("roomId")
        room = _room_key(room_id)

        await _add_temp_song(data)
        print(f"Updated current song in DB for roomId: {room_id} songUrl: {data.get('songUrl')}")

        # socket.broadcast.to — skip sender
        await sio.emit("push-song-url", {
            "songUrl": data.get("songUrl"),
            "songTitle": data.get("songTitle", ""),
            "artist": data.get("artist", ""),
            "thumbnailUrl": data.get("thumbnailUrl", ""),
            "isPlaying": True,
        }, room=room, skip_sid=sid, namespace=NAMESPACE)

    except Exception as error:
        print(f"Error in setCurrentSong: {error}")


async def _add_temp_song(data):
    """Add/replace the current song for a room in temp_room_song."""
    try:
        db = get_db()
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)

        # Delete existing
        await db.temp_room_song.delete_many({"room_id": int_room_id})

        # Insert new
        new_id = await get_next_sequence("temp_room_song")
        await db.temp_room_song.insert_one({
            "id": new_id,
            "room_id": int_room_id,
            "user_id": data.get("userId"),
            "song": data.get("songUrl", ""),
            "song_title": data.get("songTitle", ""),
            "artist": data.get("artist", ""),
            "thumbnail_url": data.get("thumbnailUrl", ""),
            "seek_position": data.get("seekPosition", ""),
            "is_playing": 1,
            "created_on": datetime.now(timezone.utc)
        })
    except Exception as error:
        print(f"Error in addTempSong: {error}")


# ── Online Chat ────────────────────────────────────────────────────

@sio.on("sendChatMessage", namespace=NAMESPACE)
async def on_send_chat_message(sid, data):
    """Receive a chat message, persist it, and broadcast to the room."""
    try:
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)
        room = _room_key(room_id)

        if int_room_id is None:
            print(f"[sendChatMessage] SKIP: missing roomId")
            return

        db = get_db()

        # Build the message document
        msg = {
            "id": data.get("id", ""),
            "sender_id": data.get("senderId"),
            "sender_name": data.get("senderName", ""),
            "text": data.get("text"),
            "file_url": data.get("fileUrl"),
            "file_name": data.get("fileName"),
            "timestamp_ms": data.get("timestampMs", int(datetime.now(timezone.utc).timestamp() * 1000)),
            "room_id": int_room_id,
        }

        # Persist to MongoDB
        await db.chat_messages.insert_one(msg)

        # Broadcast to all users in the room (including sender for confirmation)
        await sio.emit("chat-message", {
            "id": msg["id"],
            "senderId": msg["sender_id"],
            "senderName": msg["sender_name"],
            "text": msg["text"],
            "fileUrl": msg["file_url"],
            "fileName": msg["file_name"],
            "timestampMs": msg["timestamp_ms"],
            "roomId": int_room_id,
        }, room=room, namespace=NAMESPACE)

        print(f"[sendChatMessage] roomId={int_room_id} senderId={msg['sender_id']} text={msg.get('text', '')[:30]}")

    except Exception as error:
        print(f"Error in sendChatMessage: {error}")
        traceback.print_exc()


@sio.on("requestChatHistory", namespace=NAMESPACE)
async def on_request_chat_history(sid, data):
    """Send chat history for a room to the requesting socket."""
    try:
        room_id = data.get("roomId")
        int_room_id = _safe_int(room_id)

        if int_room_id is None:
            print(f"[requestChatHistory] SKIP: missing roomId")
            return

        db = get_db()

        # Fetch all messages for this room, sorted by timestamp ascending
        messages_cursor = db.chat_messages.find(
            {"room_id": int_room_id},
            {"_id": 0, "id": 1, "sender_id": 1, "sender_name": 1,
             "text": 1, "file_url": 1, "file_name": 1,
             "timestamp_ms": 1, "room_id": 1}
        ).sort("timestamp_ms", 1)

        messages_raw = await messages_cursor.to_list(None)

        # Map to camelCase keys matching the client contract
        messages = []
        for m in messages_raw:
            messages.append({
                "id": m.get("id", ""),
                "senderId": m.get("sender_id"),
                "senderName": m.get("sender_name", ""),
                "text": m.get("text"),
                "fileUrl": m.get("file_url"),
                "fileName": m.get("file_name"),
                "timestampMs": m.get("timestamp_ms"),
                "roomId": m.get("room_id"),
            })

        await sio.emit("chat-history", {"messages": messages}, room=sid, namespace=NAMESPACE)

        print(f"[requestChatHistory] roomId={int_room_id} sent {len(messages)} messages to sid={sid}")

    except Exception as error:
        print(f"Error in requestChatHistory: {error}")
        traceback.print_exc()


# ── Catch-all event logging (matches Node.js onAny / onAnyOutgoing) ──


@sio.on("*", namespace=NAMESPACE)
async def on_any_event(event, sid, data):
    """Log all incoming events (except noisy WebRTC ones)."""
    if event not in ("candidate", "answer", "offer"):
        print(f"Event Called From client----------> {event}")
