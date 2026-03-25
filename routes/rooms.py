import os
import gzip
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form
from typing import Optional

from database.mongodb import get_db, get_next_sequence
from schemas.rooms import (
    CreateRoomRequest, UpdateRoomRequest, CheckValidRoomRequest,
    DeleteRoomRequest, CheckFilenameRequest
)
from utils.responses import handle_success_response, handle_error_response
from utils.auth import get_current_user

router = APIRouter()

# Upload directory - same as Node.js UPLOAD_DIR
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


async def _join_room_internal(user_id: int, socket_id: str, room_id: int, room_name: str = "", room_url: str = ""):
    """Internal helper: insert creator into connected_users without sending HTTP response."""
    try:
        db = get_db()
        connected_user = await db.connected_users.find_one({
            "room_id": room_id,
            "user_id": user_id
        })

        if connected_user:
            await db.connected_users.update_one(
                {"id": connected_user["id"]},
                {"$set": {"is_online": 1, "socket_id": socket_id}}
            )
        else:
            new_id = await get_next_sequence("connected_users")
            await db.connected_users.insert_one({
                "id": new_id,
                "room_id": room_id,
                "user_id": user_id,
                "socket": "{}",
                "socket_id": socket_id,
                "is_online": 0,
                "is_mute": 0,
                "latitude": None,
                "longitude": None
            })
    except Exception as err:
        print(f"joinRoomInternal error for room {room_id}, user {user_id}: {err}")


async def _join_room(user_id: int, room_id: int, socket_id: str, join_room_flag: bool = False,
                     room_name: str = "", room_url: str = ""):
    """Join a user to a room. Returns response dict."""
    db = get_db()

    connected_user = await db.connected_users.find_one({
        "room_id": room_id,
        "user_id": user_id
    })

    if connected_user:
        await db.connected_users.update_one(
            {"id": connected_user["id"]},
            {"$set": {"is_online": 1, "socket_id": socket_id}}
        )
        return handle_success_response(200, "User Joined successfully")
    else:
        new_id = await get_next_sequence("connected_users")
        await db.connected_users.insert_one({
            "id": new_id,
            "room_id": room_id,
            "user_id": user_id,
            "socket": "{}",
            "socket_id": socket_id,
            "is_online": 0,
            "is_mute": 0,
            "latitude": None,
            "longitude": None
        })

        # Fetch user details for newJoiner event
        pipeline = [
            {"$match": {"room_id": room_id, "user_id": user_id}},
            {"$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "id",
                "as": "user_info"
            }},
            {"$unwind": "$user_info"},
            {"$project": {
                "id": "$user_info.id",
                "username": "$user_info.username",
                "profileImage": "$user_info.profile_pic",
                "mobile_no": "$user_info.mobile_no",
                "room_id": "$room_id",
                "isMute": "$is_mute",
                "latLng": {
                    "$concat": [
                        {"$ifNull": [{"$toString": "$latitude"}, ""]},
                        ",",
                        {"$ifNull": [{"$toString": "$longitude"}, ""]}
                    ]
                }
            }}
        ]

        results = await db.connected_users.aggregate(pipeline).to_list(1)
        if results:
            user_data = results[0]
            user_data["isMute"] = user_data.get("isMute", 0) == 1
            # Remove MongoDB _id
            user_data.pop("_id", None)

            # Emit newJoiner via socketio (imported at runtime to avoid circular imports)
            from sockets.peers import sio
            await sio.emit("newJoiner", {
                "user": user_data,
                "roomId": room_id,
            }, namespace="/webrtcPeer")
            print("Event newJoiner -------------> Emitted ")

        if join_room_flag:
            return await _update_room_info(room_id, room_name, room_url)
        else:
            return handle_success_response(200, "User Joined successfully")


async def _update_room_info(room_id: int, room_name: str = "", room_url: str = ""):
    """Get updated room with current members."""
    db = get_db()

    data = {
        "roomId": room_id,
        "roomName": room_name,
        "roomURL": room_url,
        "users": [],
    }

    # Fetch users in this room
    pipeline = [
        {"$match": {"room_id": room_id}},
        {"$lookup": {
            "from": "users",
            "localField": "user_id",
            "foreignField": "id",
            "as": "user_info"
        }},
        {"$unwind": "$user_info"},
        {"$project": {
            "_id": 0,
            "id": "$user_info.id",
            "username": "$user_info.username",
            "profileImage": "$user_info.profile_pic",
            "mobile_no": "$user_info.mobile_no",
            "room_id": "$room_id"
        }}
    ]

    users_list = await db.connected_users.aggregate(pipeline).to_list(None)
    data["users"] = users_list
    data["userCount"] = len(users_list)

    return handle_success_response(200, data)


@router.post("/createRoom")
async def create_room(body: CreateRoomRequest, user_details: dict = Depends(get_current_user)):
    try:
        room_name = body.roomName
        socket_id = body.socketId
        room_image_url = body.roomImageUrl
        location_coordinates = body.locationCoordinates
        start_address = body.startAddress
        start_coordinates = body.startCoordinates
        destination_address = body.destinationAddress
        destination_coordinates = body.destinationCoordinates
        date_of_journey = body.dateOfJourney or datetime.now(timezone.utc).strftime("%d/%m/%Y")
        user_id = user_details["id"]
        room_url = str(uuid.uuid4())

        if not (room_url and destination_address and room_name):
            return handle_error_response(400, "invalid input")

        db = get_db()
        new_room_id = await get_next_sequence("rooms")

        await db.rooms.insert_one({
            "room_id": new_room_id,
            "location_coordinates": location_coordinates,
            "room_url": room_url,
            "room_name": room_name,
            "created_by": user_id,
            "room_image_url": room_image_url,
            "start_address": start_address,
            "start_coordinates": start_coordinates,
            "destination_address": destination_address,
            "destination_coordinates": destination_coordinates,
            "date_of_journey": date_of_journey,
            "speaker": None,
            "last_spoken": None,
            "is_active": 0
        })

        # Join the creator into the new room
        await _join_room_internal(user_id, socket_id or "", new_room_id, room_name, room_url)

        return handle_success_response(
            201,
            {"roomId": new_room_id, "roomUrl": room_url},
            "Room created successfully"
        )

    except Exception as err:
        return handle_error_response(400, str(err))


@router.put("/updateRoom")
async def update_room(body: UpdateRoomRequest, user_details: dict = Depends(get_current_user)):
    try:
        room_id = body.roomId
        if not room_id:
            return handle_error_response(400, "Room ID is required")

        fields_to_update = {}
        if body.roomName:
            fields_to_update["room_name"] = body.roomName
        if body.dateOfJourney:
            fields_to_update["date_of_journey"] = body.dateOfJourney
        if body.locationCoordinates:
            fields_to_update["location_coordinates"] = body.locationCoordinates
        if body.roomImageUrl:
            fields_to_update["room_image_url"] = body.roomImageUrl
        if body.startAddress:
            fields_to_update["start_address"] = body.startAddress
        if body.startCoordinates:
            fields_to_update["start_coordinates"] = body.startCoordinates
        if body.destinationAddress:
            fields_to_update["destination_address"] = body.destinationAddress
        if body.destinationCoordinates:
            fields_to_update["destination_coordinates"] = body.destinationCoordinates

        if not fields_to_update:
            return handle_error_response(400, "No fields to update")

        db = get_db()
        result = await db.rooms.update_one(
            {"room_id": room_id},
            {"$set": fields_to_update}
        )

        if result.matched_count == 0:
            return handle_error_response(404, "Room not found")

        return handle_success_response(200, {"roomId": room_id}, "Room updated successfully")

    except Exception as err:
        return handle_error_response(500, str(err))


@router.post("/checkValidRoom")
async def check_valid_room(body: CheckValidRoomRequest, user_details: dict = Depends(get_current_user)):
    try:
        room_id = body.roomId
        user_id = user_details["id"]
        socket_id = body.socketId or ""

        if not room_id:
            return handle_error_response(400, "invalid input")

        db = get_db()
        room = await db.rooms.find_one(
            {"room_id": room_id},
            {"_id": 0, "room_id": 1, "room_name": 1, "room_url": 1}
        )

        if not room:
            return handle_error_response(404, "Room not found")

        return await _join_room(
            user_id=user_id,
            room_id=room_id,
            socket_id=socket_id,
            join_room_flag=True,
            room_name=room.get("room_name", ""),
            room_url=room.get("room_url", "")
        )

    except Exception as err:
        return handle_error_response(400, str(err))


@router.get("/roomsList")
async def get_rooms_list(user_details: dict = Depends(get_current_user)):
    try:
        user_id = user_details["id"]
        if not user_id:
            return handle_error_response(400, "User Id not valid")

        db = get_db()

        # Get all rooms the user is connected to
        connected = await db.connected_users.find(
            {"user_id": user_id},
            {"_id": 0, "room_id": 1}
        ).to_list(None)

        if not connected:
            return handle_success_response(200, {}, "You don't joined any room")

        room_ids = [c["room_id"] for c in connected]

        # Fetch room details
        rooms_cursor = db.rooms.find(
            {"room_id": {"$in": room_ids}},
            {"_id": 0}
        )
        rooms_raw = await rooms_cursor.to_list(None)

        rooms_list = []
        for room in rooms_raw:
            rooms_list.append({
                "roomId": room["room_id"],
                "createdBy": room.get("created_by"),
                "locationCoordinates": room.get("location_coordinates"),
                "roomName": room.get("room_name"),
                "startAddress": room.get("start_address"),
                "startCoordinates": room.get("start_coordinates"),
                "roomURL": room.get("room_url"),
                "roomImageUrl": room.get("room_image_url"),
                "destinationAddress": room.get("destination_address"),
                "destinationCoordinates": room.get("destination_coordinates"),
                "dateOfJourney": room.get("date_of_journey"),
                "users": [],
            })

        # Fetch all users in these rooms
        pipeline = [
            {"$match": {"room_id": {"$in": room_ids}}},
            {"$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "id",
                "as": "user_info"
            }},
            {"$unwind": "$user_info"},
            {"$project": {
                "_id": 0,
                "id": "$user_info.id",
                "username": "$user_info.username",
                "profileImage": "$user_info.profile_pic",
                "mobile_no": "$user_info.mobile_no",
                "room_id": "$room_id"
            }}
        ]

        users_list = await db.connected_users.aggregate(pipeline).to_list(None)

        # Assign users to their rooms
        for room in rooms_list:
            user_count = 0
            for user in users_list:
                if user["room_id"] == room["roomId"]:
                    room["users"].append(user)
                    user_count += 1
            room["userCount"] = user_count

        return handle_success_response(200, {"rooms": rooms_list})

    except Exception as err:
        return handle_error_response(400, str(err))


@router.delete("/deleteRoom")
async def delete_room(body: DeleteRoomRequest, user_details: dict = Depends(get_current_user)):
    room_id = body.roomId
    if not room_id:
        return handle_error_response(400, "roomId is required")

    try:
        db = get_db()
        await db.connected_users.delete_many({"room_id": room_id})
        await db.temp_room_song.delete_many({"room_id": room_id})
        await db.room_destination.delete_many({"room_id": room_id})
        await db.rooms.delete_one({"room_id": room_id})

        return handle_success_response(200, {}, "Room deleted successfully")

    except Exception as err:
        return handle_error_response(400, str(err) if isinstance(err, Exception) else "An error occurred")


@router.post("/upload-audio")
async def upload_audio_file(
    request: Request,
    audio: UploadFile = File(...),
    thumbnail: Optional[UploadFile] = File(None),
    user_details: dict = Depends(get_current_user)
):
    try:
        if not audio:
            return handle_error_response(400, "No audio file uploaded")

        # Handle audio file
        original_audio_name = audio.filename
        if original_audio_name.endswith(".gz"):
            original_audio_name = original_audio_name[:-3]

        audio_output_path = os.path.join(UPLOAD_DIR, original_audio_name)

        # Read and decompress the gzip audio
        audio_bytes = await audio.read()
        decompressed_buffer = gzip.decompress(audio_bytes)

        with open(audio_output_path, "wb") as f:
            f.write(decompressed_buffer)

        # Build the audio URL
        base_url = str(request.base_url).rstrip("/")
        audio_url = f"{base_url}/uploads/{original_audio_name}"

        # Handle thumbnail file (optional)
        thumbnail_url = None
        thumbnail_name = None
        if thumbnail and thumbnail.filename:
            thumbnail_name = thumbnail.filename
            thumbnail_output_path = os.path.join(UPLOAD_DIR, thumbnail_name)
            thumbnail_url = f"{base_url}/uploads/{thumbnail_name}"

            thumbnail_bytes = await thumbnail.read()
            with open(thumbnail_output_path, "wb") as f:
                f.write(thumbnail_bytes)

        # Save to DB
        db = get_db()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        new_id = await get_next_sequence("uploaded_songs")

        await db.uploaded_songs.insert_one({
            "id": new_id,
            "song_url": audio_url,
            "song_name": original_audio_name,
            "thumbnail_url": thumbnail_url,
            "thumbnail_name": thumbnail_name,
            "last_used": now
        })

        return handle_success_response(200, {"url": audio_url, "thumbnailUrl": thumbnail_url})

    except Exception as error:
        print(f"Error in uploading audio: {error}")
        return handle_error_response(500, "Server error while uploading files")


@router.post("/checkFilename")
async def check_audio_file_exists(
    request: Request,
    body: CheckFilenameRequest,
    user_details: dict = Depends(get_current_user)
):
    try:
        filename = body.filename
        if not filename:
            return handle_error_response(400, "Filename is required")

        file_path = os.path.join(UPLOAD_DIR, filename)

        if os.path.exists(file_path):
            db = get_db()
            song_details = await db.uploaded_songs.find_one({"song_name": filename})

            if song_details:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                await db.uploaded_songs.update_one(
                    {"id": song_details["id"]},
                    {"$set": {"last_used": now}}
                )

                base_url = str(request.base_url).rstrip("/")
                return handle_success_response(200, {
                    "isExists": True,
                    "filePath": file_path,
                    "url": f"{base_url}/uploads/{filename}",
                    "thumbnailUrl": f"{base_url}/uploads/{song_details.get('thumbnail_name', '')}",
                })
            else:
                base_url = str(request.base_url).rstrip("/")
                return handle_success_response(200, {
                    "isExists": True,
                    "filePath": file_path,
                    "url": f"{base_url}/uploads/{filename}",
                    "thumbnailUrl": None,
                })
        else:
            return handle_success_response(200, {
                "isExists": False,
                "error": "File not found"
            })

    except Exception as error:
        print(f"Error checking file: {error}")
        return handle_error_response(500, "Server error while checking file")
