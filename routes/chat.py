import os
import uuid

from fastapi import APIRouter, Depends, Request, UploadFile, File

from utils.responses import handle_success_response, handle_error_response
from utils.auth import get_current_user

router = APIRouter()

# Upload directory — same as the main uploads folder
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "chat")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/upload-chat-file")
async def upload_chat_file(
    request: Request,
    file: UploadFile = File(...),
    user_details: dict = Depends(get_current_user)
):
    try:
        if not file or not file.filename:
            return handle_error_response(400, "No file uploaded")

        # Generate a unique filename to avoid collisions
        ext = os.path.splitext(file.filename)[1]
        unique_name = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(UPLOAD_DIR, unique_name)

        # Save the file
        file_bytes = await file.read()
        with open(file_path, "wb") as f:
            f.write(file_bytes)

        # Build the public URL
        base_url = str(request.base_url).rstrip("/")
        file_url = f"{base_url}/uploads/chat/{unique_name}"

        return handle_success_response(200, {"url": file_url})

    except Exception as error:
        print(f"Error in upload-chat-file: {error}")
        return handle_error_response(500, "Server error while uploading file")
