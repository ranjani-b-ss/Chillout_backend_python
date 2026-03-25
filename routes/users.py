from fastapi import APIRouter, Depends

from database.mongodb import get_db, get_next_sequence
from schemas.users import EditUserRequest, AddChatRecordRequest, GetChatRecordsRequest
from utils.responses import handle_success_response, handle_error_response
from utils.auth import get_current_user

router = APIRouter()


@router.put("/user/{user_id}")
async def edit_user(user_id: int, body: EditUserRequest, user_details: dict = Depends(get_current_user)):
    try:
        db = get_db()
        user = await db.users.find_one({"id": user_id})

        if not user:
            return handle_error_response(404, "User not found")

        username = body.userName or user.get("username", "")
        mobile_no = body.mobileNo or user.get("mobile_no", "")
        # Accept base64-encoded image string directly from the client
        profile_image = body.profileImage or user.get("profile_pic")

        await db.users.update_one(
            {"id": user_id},
            {"$set": {
                "username": username,
                "mobile_no": mobile_no,
                "profile_pic": profile_image
            }}
        )

        return handle_success_response(200, {}, "User updated successfully")

    except Exception as err:
        return handle_error_response(400, str(err))


@router.get("/user/{user_id}")
async def get_user(user_id: int, user_details: dict = Depends(get_current_user)):
    try:
        db = get_db()
        user = await db.users.find_one(
            {"id": user_id},
            {"_id": 0, "id": 1, "username": 1, "mobile_no": 1, "profile_pic": 1}
        )

        if not user:
            return handle_error_response(404, "User not found")

        data = {
            "id": user["id"],
            "username": user.get("username", ""),
            "mobileNo": user.get("mobile_no", ""),
            "profileImage": user.get("profile_pic")
        }

        return handle_success_response(200, data)

    except Exception as err:
        return handle_error_response(400, str(err))


@router.get("/getPersonalDetails")
async def get_personal_details(user_details: dict = Depends(get_current_user)):
    try:
        user_id = user_details["id"]
        if not user_id:
            return handle_error_response(400, "Invalid input")

        db = get_db()
        user = await db.users.find_one(
            {"id": user_id},
            {"_id": 0, "id": 1, "username": 1, "mobile_no": 1, "profile_pic": 1}
        )

        if not user:
            return handle_error_response(404, "User not found")

        user_data = {
            "userId": user["id"],
            "username": user.get("username", ""),
            "mobileNo": user.get("mobile_no", ""),
            "profilePicture": user.get("profile_pic")
        }

        return handle_success_response(200, user_data, "User Details fetched successfully")

    except Exception as err:
        return handle_error_response(500, str(err))


@router.post("/addChatRecords")
async def add_chat_records(body: AddChatRecordRequest, user_details: dict = Depends(get_current_user)):
    try:
        db = get_db()
        new_id = await get_next_sequence("chat_records")

        await db.chat_records.insert_one({
            "id": new_id,
            "room_id": body.roomId,
            "user_name": body.userName,
            "record_url": body.recordUrl,
            "created_at": body.createdAt
        })

        return handle_success_response(200, {"message": "Chat record added successfully"})

    except Exception as err:
        return handle_error_response(500, str(err))


@router.get("/getChatRecords")
async def get_chat_records(body: GetChatRecordsRequest, user_details: dict = Depends(get_current_user)):
    try:
        db = get_db()
        records_cursor = db.chat_records.find(
            {"room_id": body.roomId},
            {"_id": 0, "room_id": 1, "user_name": 1, "record_url": 1, "created_at": 1}
        )
        records = await records_cursor.to_list(None)

        user_records = []
        for record in records:
            user_records.append({
                "roomId": record["room_id"],
                "userName": record["user_name"],
                "recordUrl": record["record_url"],
                "createdAt": record["created_at"]
            })

        if user_records:
            return handle_success_response(200, user_records, "User Records fetched successfully")
        else:
            return handle_success_response(400, [], "No rooms are found for the chat")

    except Exception as err:
        return handle_error_response(500, str(err))
