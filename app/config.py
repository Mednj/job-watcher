import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database: str = "data/jobs.sqlite3"
    telegram_token: str = ""
    telegram_chat_id: str = ""
    access_token: str = ""
    source_gap: float = 15
    http_timeout: float = 20

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    @classmethod
    def from_env(cls):
        load_dotenv()
        return cls(
            database=os.getenv("JOB_WATCHER_DB", "data/jobs.sqlite3"),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            access_token=os.getenv("APP_ACCESS_TOKEN", "").strip(),
            source_gap=max(15, float(os.getenv("SOURCE_REQUEST_GAP_SECONDS", "15"))),
            http_timeout=max(5, float(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))),
        )
