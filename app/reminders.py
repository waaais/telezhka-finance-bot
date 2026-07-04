import asyncio
import logging
from calendar import monthrange
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.bot.formatters import (
    daily_evotor_summary,
    stats_message,
    weekly_salary_message,
)
from app.config import Settings
from app.services import FinanceService

logger = logging.getLogger(__name__)

REMINDER_TEXT = (
    "⏰ Напоминание\n\n"
    "Пожалуйста, внесите выручку за сегодняшний день.\n\n"
    "Пример: `Ксюша нал 12500 безнал 38640`"
)

SERVER_PAYMENT_REMINDER_TEXT = (
    "💳 Напоминание\n\n"
    "Сегодня 1 число. Нужно проверить и оплатить сервер, чтобы бот продолжал работать 24/7."
)


async def run_scheduled_notifications(
    bot: Bot,
    finance_service: FinanceService,
    settings: Settings,
) -> None:
    await asyncio.gather(
        run_daily_revenue_reminders(bot, finance_service, settings),
        run_weekly_report_reminders(bot, finance_service, settings),
        run_server_payment_reminders(bot, finance_service, settings),
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
        await _send_revenue_reminders(bot, finance_service, settings, next_run.date())


async def run_weekly_report_reminders(
    bot: Bot,
    finance_service: FinanceService,
    settings: Settings,
) -> None:
    if not settings.weekly_report_enabled:
        logger.info("Weekly report reminders are disabled")
        return

    timezone = ZoneInfo(settings.timezone)
    while True:
        next_run = _next_weekly_at(
            now=datetime.now(timezone),
            weekday=settings.weekly_report_weekday,
            hour_minute=settings.weekly_report_hour_minute,
        )
        sleep_seconds = max(1, (next_run - datetime.now(timezone)).total_seconds())
        logger.info("Weekly report scheduled", extra={"next_run": next_run.isoformat()})
        await asyncio.sleep(sleep_seconds)
        await _send_weekly_reports(bot, finance_service, next_run.date())


async def run_server_payment_reminders(
    bot: Bot,
    finance_service: FinanceService,
    settings: Settings,
) -> None:
    if not settings.server_payment_reminder_enabled:
        logger.info("Server payment reminders are disabled")
        return

    timezone = ZoneInfo(settings.timezone)
    while True:
        next_run = _next_monthly_at(
            now=datetime.now(timezone),
            day=settings.server_payment_reminder_day,
            hour_minute=settings.server_payment_reminder_hour_minute,
        )
        sleep_seconds = max(1, (next_run - datetime.now(timezone)).total_seconds())
        logger.info("Server payment reminder scheduled", extra={"next_run": next_run.isoformat()})
        await asyncio.sleep(sleep_seconds)
        await _send_server_payment_reminders(bot, finance_service)


async def _send_revenue_reminders(
    bot: Bot,
    finance_service: FinanceService,
    settings: Settings,
    today: date,
) -> None:
    chat_ids = await finance_service.reminder_chat_ids()
    if not chat_ids:
        logger.info("No chats for daily revenue reminder")
        return

    for chat_id in chat_ids:
        try:
            if settings.evotor_enabled:
                result = await finance_service.import_evotor_revenue(
                    chat_id=chat_id,
                    message_id=-int(today.strftime("%Y%m%d")),
                    today=today,
                    skip_if_exists=False,
                )
                if result.entry is not None:
                    await bot.send_message(
                        chat_id,
                        daily_evotor_summary(
                            result.entry,
                            updated=result.updated,
                            sheet_error=bool(result.sheet_error),
                        ),
                    )
                    continue
                if result.duplicate:
                    continue
                await bot.send_message(
                    chat_id,
                    (result.parse_error or "Не смог получить выручку из Эвотора.")
                    + "\n\n"
                    + REMINDER_TEXT,
                )
                continue
            if await finance_service.has_finance_entry(chat_id, today):
                logger.info(
                    "Daily revenue reminder skipped because entry already exists",
                    extra={"chat_id": chat_id, "entry_date": today.isoformat()},
                )
                continue
            await bot.send_message(chat_id, REMINDER_TEXT, parse_mode="Markdown")
        except Exception:
            logger.exception(
                "Failed to send daily revenue reminder",
                extra={"chat_id": chat_id},
            )


async def _send_weekly_reports(bot: Bot, finance_service: FinanceService, today: date) -> None:
    chat_ids = await finance_service.reminder_chat_ids()
    if not chat_ids:
        logger.info("No chats for weekly report")
        return

    period, totals = await finance_service.statistics_for_week(today)
    salary_period, salary_totals = await finance_service.weekly_salary_breakdown(today)
    text = (
        "📅 Еженедельный отчет\n\n"
        + stats_message(period, totals)
        + "\n\n"
        + weekly_salary_message(salary_period, salary_totals)
    )
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logger.exception(
                "Failed to send weekly report",
                extra={"chat_id": chat_id},
            )


async def _send_server_payment_reminders(bot: Bot, finance_service: FinanceService) -> None:
    chat_ids = await finance_service.reminder_chat_ids()
    if not chat_ids:
        logger.info("No chats for server payment reminder")
        return

    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, SERVER_PAYMENT_REMINDER_TEXT)
        except Exception:
            logger.exception(
                "Failed to send server payment reminder",
                extra={"chat_id": chat_id},
            )


def _next_reminder_at(now: datetime, hour_minute: tuple[int, int]) -> datetime:
    hour, minute = hour_minute
    planned = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if planned <= now:
        planned += timedelta(days=1)
    return planned


def _next_weekly_at(now: datetime, weekday: int, hour_minute: tuple[int, int]) -> datetime:
    hour, minute = hour_minute
    days_ahead = (weekday - now.weekday()) % 7
    planned_date = now.date() + timedelta(days=days_ahead)
    planned = datetime.combine(planned_date, datetime.min.time(), tzinfo=now.tzinfo)
    planned = planned.replace(hour=hour, minute=minute)
    if planned <= now:
        planned += timedelta(days=7)
    return planned


def _next_monthly_at(now: datetime, day: int, hour_minute: tuple[int, int]) -> datetime:
    hour, minute = hour_minute
    planned_day = min(day, monthrange(now.year, now.month)[1])
    planned = now.replace(day=planned_day, hour=hour, minute=minute, second=0, microsecond=0)
    if planned <= now:
        year = now.year + int(now.month == 12)
        month = 1 if now.month == 12 else now.month + 1
        planned_day = min(day, monthrange(year, month)[1])
        planned = planned.replace(year=year, month=month, day=planned_day)
    return planned
