import os
import json
import firebase_admin
from firebase_admin import credentials, messaging

_app = None


def initialize_firebase():
    """Initialize Firebase Admin SDK using the service account key."""
    global _app
    if _app:
        return

    # Look for serviceAccountKey.json in the project root or parent directory
    service_account_path = os.getenv(
        "FIREBASE_SERVICE_ACCOUNT_KEY",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "serviceAccountKey.json")
    )

    if os.path.exists(service_account_path):
        cred = credentials.Certificate(service_account_path)
        _app = firebase_admin.initialize_app(cred)
        print("Firebase initialized ✅")
    else:
        print("⚠️  serviceAccountKey.json not found. Firebase notifications disabled.")


async def send_fcm_message(token: str, notification: dict = None, data: dict = None, android: dict = None):
    """Send an FCM message to a specific device token."""
    message_kwargs = {"token": token}

    if notification:
        message_kwargs["notification"] = messaging.Notification(
            title=notification.get("title"),
            body=notification.get("body")
        )

    if data:
        message_kwargs["data"] = data

    if android:
        android_config_kwargs = {}
        if "data" in android:
            android_config_kwargs["data"] = android["data"]
        message_kwargs["android"] = messaging.AndroidConfig(**android_config_kwargs)

    message = messaging.Message(**message_kwargs)
    return messaging.send(message)
