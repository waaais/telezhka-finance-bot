from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/finance.db",
        alias="DATABASE_URL",
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    default_salary: int = Field(default=2500, alias="DEFAULT_SALARY")
    low_salary: int = Field(default=2000, alias="LOW_SALARY")
    low_salary_employees: str = Field(
        default="Ксюша,Настя,Кристина",
        alias="LOW_SALARY_EMPLOYEES",
    )
    admin_chat_ids: str = Field(default="", alias="ADMIN_CHAT_IDS")
    timezone: str = Field(default="Europe/Moscow", alias="TIMEZONE")
    daily_reminder_enabled: bool = Field(default=True, alias="DAILY_REMINDER_ENABLED")
    daily_reminder_time: str = Field(default="22:30", alias="DAILY_REMINDER_TIME")
    google_sheets_enabled: bool = Field(default=False, alias="GOOGLE_SHEETS_ENABLED")
    google_sheets_spreadsheet_id: str = Field(default="", alias="GOOGLE_SHEETS_SPREADSHEET_ID")
    google_sheets_credentials_file: str = Field(
        default="./google-service-account.json",
        alias="GOOGLE_SHEETS_CREDENTIALS_FILE",
    )
    google_sheets_write_mode: str = Field(default="monthly_sheet", alias="GOOGLE_SHEETS_WRITE_MODE")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def low_salary_names(self) -> list[str]:
        return [name.strip() for name in self.low_salary_employees.split(",") if name.strip()]

    @property
    def admin_ids(self) -> set[int]:
        ids: set[int] = set()
        for value in self.admin_chat_ids.split(","):
            value = value.strip()
            if value:
                ids.add(int(value))
        return ids

    @property
    def reminder_hour_minute(self) -> tuple[int, int]:
        hour_text, minute_text = self.daily_reminder_time.split(":", maxsplit=1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("DAILY_REMINDER_TIME must be HH:MM")
        return hour, minute

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def ensure_local_dirs(self) -> None:
        if self.is_sqlite and ":///" in self.database_url:
            db_path = self.database_url.rsplit(":///", maxsplit=1)[-1]
            if db_path.startswith("./"):
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
