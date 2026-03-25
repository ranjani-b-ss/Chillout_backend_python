import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    JWT_SECRET_KEY: str = os.getenv(
        "JWT_SECRET_KEY",
        "jhdtgfjyyktdykitdoydtuvnbvhjfyur67567845635xds5tu4chgmkiju796vghjo8li6r7"
    )
    FAST2SMS: str = os.getenv("FAST2SMS", "")
    PORT: int = int(os.getenv("PORT", "3001"))
    MONGO_URL: str = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "chill_out")


settings = Settings()
