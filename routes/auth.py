import math
import random
from datetime import datetime, timezone

from fastapi import APIRouter

from database.mongodb import get_db, get_next_sequence
from schemas.auth import LoginRequest, VerifyOtpRequest
from utils.responses import handle_success_response, handle_error_response
from utils.auth import create_jwt_token

router = APIRouter()


@router.post("/login")
async def login(body: LoginRequest):
    try:
        mobile_no = body.mobileNo
        username = body.username or ""

        if not mobile_no:
            return handle_error_response(400, "invalid input")

        db = get_db()
        otp = str(math.floor(1000 + random.random() * 9000))

        user = await db.users.find_one({"mobile_no": mobile_no})

        if user:
            await db.users.update_one(
                {"id": user["id"]},
                {"$set": {
                    "otp": otp,
                    "otp_created_time": datetime.now(timezone.utc)
                }}
            )
            # const response = await commonUtils.sendSMS(message, [mobileNo]);
            print(f"otp  ==> {otp} for mobile ==> {mobile_no}")
            return handle_success_response(200, {"otp": otp}, "otp send successfully")
        else:
            new_id = await get_next_sequence("users")
            await db.users.insert_one({
                "id": new_id,
                "username": username,
                "mobile_no": mobile_no,
                "profile_pic": None,
                "otp": otp,
                "otp_created_time": datetime.now(timezone.utc),
                "fcm_token": None,
                "password": None
            })
            # const response = await commonUtils.sendSMS(message, [mobileNo]);
            print(f"otp  ==> {otp} for mobile ==> {mobile_no}")
            return handle_success_response(200, {"otp": otp}, "otp send successfully")

    except Exception as err:
        return handle_error_response(400, str(err))


@router.post("/verifyOtp")
async def verify_otp(body: VerifyOtpRequest):
    try:
        mobile_no = body.mobileNo
        user_otp = body.otp
        fcm_token = body.fcmToken

        if not mobile_no or not user_otp:
            return handle_error_response(400, "invalid input")

        db = get_db()
        user = await db.users.find_one({"mobile_no": mobile_no})

        if not user:
            return handle_error_response(404, "User not found")

        if user.get("otp") != user_otp:
            return handle_error_response(400, "Invalid Otp")

        # Check OTP expiration (2 minutes)
        otp_created_time = user.get("otp_created_time")
        if otp_created_time:
            now = datetime.now(timezone.utc)
            # Ensure otp_created_time is timezone-aware
            if otp_created_time.tzinfo is None:
                otp_created_time = otp_created_time.replace(tzinfo=timezone.utc)
            otp_expire_time = (now - otp_created_time).total_seconds() * 1000

            if otp_expire_time >= 120000:
                return handle_error_response(400, "Otp expired")
        else:
            return handle_error_response(400, "Otp expired")

        # Clear OTP and update FCM token
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {
                "otp": None,
                "otp_created_time": None,
                "fcm_token": fcm_token
            }}
        )

        # Generate JWT token
        token_data = create_jwt_token({
            "id": user["id"],
            "username": user.get("username", ""),
            "mobileNo": user["mobile_no"]
        })

        data = {
            "token": token_data,
            "userId": user["id"],
            "username": user.get("username", ""),
            "mobileNo": user["mobile_no"],
            "profilePicture": user.get("profile_pic")
        }

        return handle_success_response(200, data)

    except Exception as err:
        return handle_error_response(400, str(err))
