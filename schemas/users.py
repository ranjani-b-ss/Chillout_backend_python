from pydantic import BaseModel
from typing import Optional


class EditUserRequest(BaseModel):
    userName: Optional[str] = None
    mobileNo: Optional[str] = None
    profileImage: Optional[str] = None


class AddChatRecordRequest(BaseModel):
    userName: str
    roomId: int
    createdAt: str
    recordUrl: str


class GetChatRecordsRequest(BaseModel):
    roomId: int
