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
    weekly_report_enabled: bool = Field(default=True, alias="WEEKLY_REPORT_ENABLED")
    weekly_report_time: str = Field(default="23:00", alias="WEEKLY_REPORT_TIME")
    weekly_report_weekday: int = Field(default=6, alias="WEEKLY_REPORT_WEEKDAY")
    server_payment_reminder_enabled: bool = Field(
        default=True,
        alias="SERVER_PAYMENT_REMINDER_ENABLED",
    )
    server_payment_reminder_day: int = Field(default=1, alias="SERVER_PAYMENT_REMINDER_DAY")
    server_payment_reminder_time: str = Field(
        default="10:00",
        alias="SERVER_PAYMENT_REMINDER_TIME",
    )
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
        return _parse_hour_minute(self.daily_reminder_time, "DAILY_REMINDER_TIME")

    @property
    def weekly_report_hour_minute(self) -> tuple[int, int]:
        if not 0 <= self.weekly_report_weekday <= 6:
            raise ValueError("WEEKLY_REPORT_WEEKDAY must be 0..6")
        return _parse_hour_minute(self.weekly_report_time, "WEEKLY_REPORT_TIME")

    @property
    def server_payment_reminder_hour_minute(self) -> tuple[int, int]:
        if not 1 <= self.server_payment_reminder_day <= 28:
            raise ValueError("SERVER_PAYMENT_REMINDER_DAY must be 1..28")
        return _parse_hour_minute(
            self.server_payment_reminder_time,
            "SERVER_PAYMENT_REMINDER_TIME",
        )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def ensure_local_dirs(self) -> None:
        if self.is_sqlite and ":///" in self.database_url:
            db_path = self.database_url.rsplit(":///", maxsplit=1)[-1]
            if db_path.startswith("./"):
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def _parse_hour_minute(value: str, field_name: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", maxsplit=1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be HH:MM") from exc

    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{field_name} must be HH:MM")
    return hour, minute


@lru_cache
def get_settings() -> Settings:
    return Settings()
