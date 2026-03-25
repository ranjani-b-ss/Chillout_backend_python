import os
import uvicorn
import socketio
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database.mongodb import connect_to_mongo, close_mongo_connection
from database.config import settings
from utils.firebase import initialize_firebase

from routes.auth import router as auth_router
from routes.rooms import router as rooms_router
from routes.users import router as users_router
from routes.notifications import router as notifications_router
from routes.chat import router as chat_router

from sockets.peers import sio

# Load environment variables
load_dotenv()


# Lifespan event handler (replaces deprecated on_event)
@asynccontextmanager
async def lifespan(_app):
    # Startup
    await connect_to_mongo()
    initialize_firebase()
    yield
    # Shutdown
    await close_mongo_connection()


# Create FastAPI app
app = FastAPI(
    title="Travel Space API",
    description="Collaborative travel room application API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware (same as cors() in Express)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Root health check (same as app.get("/"))
@app.get("/")
async def root():
    return "Hello Developer"


# Mount all API routes under /api prefix (same as app.use("/api", router))
app.include_router(auth_router, prefix="/api")
app.include_router(rooms_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(chat_router, prefix="/api")

# Health check route (same as router.get("/health"))
@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

# API root (same as router.get("/"))
@app.get("/api/")
async def api_root():
    return "App Works"


# Serve uploaded files as static (same as app.use('/uploads', express.static(...)))
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# Wrap FastAPI app with Socket.IO ASGI app
# socketio_path must match the Node.js path: "/io/webrtc"
socket_app = socketio.ASGIApp(sio, app, socketio_path="/io/webrtc")

if __name__ == "__main__":
    port = settings.PORT
    print(f"Server is running on port {port}")
    uvicorn.run(
        socket_app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
