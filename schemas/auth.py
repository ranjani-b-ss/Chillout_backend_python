from pydantic import BaseModel
from typing import Optional


class LoginRequest(BaseModel):
    mobileNo: str
    username: Optional[str] = ""


class VerifyOtpRequest(BaseModel):
    mobileNo: str
    otp: str
    fcmToken: Optional[str] = None


class LoginResponse(BaseModel):
    otp: str


class VerifyOtpResponse(BaseModel):
    token: str
    userId: int
    username: str
    mobileNo: str
    profilePicture: Optional[str] = None
