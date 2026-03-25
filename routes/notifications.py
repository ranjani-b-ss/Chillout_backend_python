from fastapi import APIRouter, Depends

from database.mongodb import get_db
from schemas.notifications import SendNotificationRequest
from utils.responses import handle_success_response, handle_error_response
from utils.auth import get_current_user
from utils.firebase import send_fcm_message

router = APIRouter()


@router.post("/notification")
async def send_notification(body: SendNotificationRequest, user_details: dict = Depends(get_current_user)):
    try:
        db = get_db()
        user = await db.users.find_one(
            {"id": body.userId},
            {"_id": 0, "fcm_token": 1}
        )

        if not user or not user.get("fcm_token"):
            return handle_error_response(400, "Invalid Fcm Token")

        token = user["fcm_token"]

        await send_fcm_message(
            token=token,
            notification={
                "title": body.messageTitle,
                "body": body.messageBody
            }
        )

        return handle_success_response(200, "", "Notification send successfully")

    except Exception:
        return handle_error_response(401, "Un Authroized")
