from pydantic import BaseModel
from typing import Optional


class CreateRoomRequest(BaseModel):
    roomName: str
    socketId: Optional[str] = None
    roomImageUrl: Optional[str] = None
    locationCoordinates: Optional[str] = None
    startAddress: Optional[str] = None
    startCoordinates: Optional[str] = None
    destinationAddress: str
    destinationCoordinates: Optional[str] = None
    dateOfJourney: Optional[str] = None


class UpdateRoomRequest(BaseModel):
    roomId: int
    roomName: Optional[str] = None
    dateOfJourney: Optional[str] = None
    locationCoordinates: Optional[str] = None
    roomImageUrl: Optional[str] = None
    startAddress: Optional[str] = None
    startCoordinates: Optional[str] = None
    destinationAddress: Optional[str] = None
    destinationCoordinates: Optional[str] = None


class CheckValidRoomRequest(BaseModel):
    roomId: int
    socketId: Optional[str] = None


class DeleteRoomRequest(BaseModel):
    roomId: int


class CheckFilenameRequest(BaseModel):
    filename: str
