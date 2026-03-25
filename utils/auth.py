import jwt
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database.config import settings


security = HTTPBearer()


def create_jwt_token(data: dict) -> str:
    """Create a JWT token with the given data payload."""
    payload = {"data": data}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


def verify_jwt_token(token: str):
    """Verify a JWT token and return the decoded data, or None if invalid."""
    try:
        decoded = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
        return decoded
    except jwt.PyJWTError:
        return None


async def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency that extracts and validates the JWT from the
    Authorization header. Returns the user details dict.
    Mirrors the Node.js auth middleware behavior.
    """
    try:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid request!")

        token = auth_header.split(" ")[1]
        decoded_data = verify_jwt_token(token)

        if decoded_data and decoded_data.get("data"):
            return decoded_data["data"]
        else:
            raise HTTPException(status_code=401, detail="Malformed User")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid request!")
