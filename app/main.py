import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import router
from app.config import get_settings
from app.integrations.evotor import create_evotor_sync
from app.integrations.google_sheets import create_sheet_sync
from app.logging_config import configure_logging
from app.reminders import run_scheduled_notifications
from app.salary.engine import SalaryEngine
from app.services import FinanceService
from app.storage.database import create_engine, create_session_factory, init_db

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await init_db(engine)

    sheet_sync = create_sheet_sync(settings)
    evotor_sync = create_evotor_sync(settings)
    finance_service = FinanceService(
        session_factory,
        settings,
        SalaryEngine(),
        sheet_sync,
        evotor_sync,
    )
    await finance_service.seed_default_employees()
    await finance_service.seed_known_reminder_chats()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(finance_service=finance_service, settings=settings)
    dispatcher.include_router(router)

    logger.info("Bot started")
    await bot.delete_webhook(drop_pending_updates=False)
    notification_task = asyncio.create_task(
        run_scheduled_notifications(bot, finance_service, settings),
        name="scheduled-notifications",
    )
    try:
        await dispatcher.start_polling(
            bot,
            handle_as_tasks=False,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        notification_task.cancel()
        try:
            await notification_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
