import httpx
from database.config import settings


async def send_sms(message: str, mobile_no: str):
    """Send SMS via fast2sms API."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://www.fast2sms.com/dev/bulkV2",
                headers={
                    "authorization": settings.FAST2SMS
                },
                json={
                    "message": message,
                    "language": "english",
                    "route": "q",
                    "numbers": mobile_no
                }
            )
            return response.json()
    except Exception as e:
        raise Exception(str(e))
