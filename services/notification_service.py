import json
from database.mongodb import get_db
from utils.firebase import send_fcm_message


async def send_notification_for_offline_users(room_id: int, notification_message: str, message_type: str):
    """Send FCM notification to all offline users in a room.
    Mirrors notificationController.sendNotificationForOfflineUsers from Node.js."""
    try:
        db = get_db()

        # Fetch offline users in the room with room details
        pipeline = [
            {"$match": {"room_id": room_id, "is_online": 0}},
            {"$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "id",
                "as": "user_info"
            }},
            {"$unwind": {"path": "$user_info", "preserveNullAndEmptyArrays": True}},
            {"$lookup": {
                "from": "rooms",
                "localField": "room_id",
                "foreignField": "room_id",
                "as": "room_info"
            }},
            {"$unwind": {"path": "$room_info", "preserveNullAndEmptyArrays": True}},
            {"$project": {
                "_id": 0,
                "userId": "$user_id",
                "fcmToken": "$user_info.fcm_token",
                "roomId": "$room_info.room_id",
                "createdBy": "$room_info.created_by",
                "locationCoordinates": "$room_info.location_coordinates",
                "roomName": "$room_info.room_name",
                "roomURL": "$room_info.room_url",
                "roomImageUrl": "$room_info.room_image_url",
                "destinationAddress": "$room_info.destination_address",
                "dateOfJourney": "$room_info.date_of_journey",
            }}
        ]

        offline_users = await db.connected_users.aggregate(pipeline).to_list(None)

        if not offline_users:
            return

        # Build room data payload
        first = offline_users[0]
        final_room_data = {
            "roomId": room_id,
            "roomName": first.get("roomName"),
            "locationCoordinates": first.get("locationCoordinates"),
            "roomUrl": first.get("roomURL"),
            "roomImageUrl": first.get("roomImageUrl"),
            "destinationAddress": first.get("destinationAddress"),
            "dateOfJourney": first.get("dateOfJourney"),
            "userCount": 0,
            "users": [],
            "createdBy": first.get("createdBy"),
        }

        # Fetch all users in the room
        user_pipeline = [
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

        users_list = await db.connected_users.aggregate(user_pipeline).to_list(None)

        user_count = 0
        for user in users_list:
            if user.get("room_id") == room_id:
                final_room_data["users"].append(user)
                user_count += 1
        final_room_data["userCount"] = user_count

        # Send notification to each offline user
        for offline_user in offline_users:
            fcm_token = offline_user.get("fcmToken")
            if fcm_token and fcm_token != "":
                try:
                    await send_fcm_message(
                        token=fcm_token,
                        data={
                            "roomData": json.dumps(final_room_data, default=str),
                            "title": "Walkie Talkie",
                            "body": notification_message,
                        },
                        android={
                            "data": {
                                "type": message_type,
                            }
                        }
                    )
                except Exception:
                    pass

    except Exception as error:
        print(f"sendNotificationForOfflineUsers error: {error}")
