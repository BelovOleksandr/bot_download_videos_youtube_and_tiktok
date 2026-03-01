from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

@dataclass
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "8410475133:AAHNWZDqdvnVJYx6QX196-xlCLKHhsgIcpE")
    LOCAL_BOT_API_URL: str | None = os.getenv("LOCAL_BOT_API_URL")
    EXTERNAL_BASE_URL: str | None = os.getenv("EXTERNAL_BASE_URL")
    FILE_SERVER_PORT: int = int(os.getenv("FILE_SERVER_PORT", "8088"))
    DOWNLOAD_DIR: Path = BASE_DIR / "downloads"
    MAX_STD_API_BYTES: int = 49 * 1024 * 1024

settings = Settings()
settings.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)