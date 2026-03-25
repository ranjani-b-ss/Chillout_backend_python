from pydantic import BaseModel


class SendNotificationRequest(BaseModel):
    userId: int
    messageTitle: str
    messageBody: str
