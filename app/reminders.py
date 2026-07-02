import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.config import Settings
from app.services import FinanceService

logger = logging.getLogger(__name__)

REMINDER_TEXT = (
    "⏰ Напоминание\n\n"
    "Пожалуйста, внесите выручку за сегодняшний день.\n\n"
    "Пример: `Ксюша нал 12500 безнал 38640`"
)


async def run_daily_revenue_reminders(
    bot: Bot,
    finance_service: FinanceService,
    settings: Settings,
) -> None:
    if not settings.daily_reminder_enabled:
        logger.info("Daily revenue reminders are disabled")
        return

    timezone = ZoneInfo(settings.timezone)
    while True:
        next_run = _next_reminder_at(
            now=datetime.now(timezone),
            hour_minute=settings.reminder_hour_minute,
        )
        sleep_seconds = max(1, (next_run - datetime.now(timezone)).total_seconds())
        logger.info("Daily revenue reminder scheduled", extra={"next_run": next_run.isoformat()})
        await asyncio.sleep(sleep_seconds)
        await _send_revenue_reminders(bot, finance_service)


async def _send_revenue_reminders(bot: Bot, finance_service: FinanceService) -> None:
    chat_ids = await finance_service.reminder_chat_ids()
    if not chat_ids:
        logger.info("No chats for daily revenue reminder")
        return

    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, REMINDER_TEXT, parse_mode="Markdown")
        except Exception:
            logger.exception(
                "Failed to send daily revenue reminder",
                extra={"chat_id": chat_id},
            )


def _next_reminder_at(now: datetime, hour_minute: tuple[int, int]) -> datetime:
    hour, minute = hour_minute
    planned = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if planned <= now:
        planned += timedelta(days=1)
    return planned
