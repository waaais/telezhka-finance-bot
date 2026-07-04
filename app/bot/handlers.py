import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot.formatters import (
    duplicate_message,
    employees_message,
    help_message,
    stats_message,
    success_message,
    success_with_sheet_warning,
    update_message,
    weekly_salary_message,
)
from app.config import Settings
from app.parser.finance_parser import current_date
from app.services import FinanceService

logger = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
async def start(message: Message, finance_service: FinanceService) -> None:
    await finance_service.remember_chat(message.chat.id)
    await message.answer(
        "Привет. Я записываю ежедневную выручку сотрудников и считаю зарплаты.\n\n"
        + help_message(),
        parse_mode="Markdown",
    )


@router.message(Command("help"))
async def help_command(message: Message, finance_service: FinanceService) -> None:
    await finance_service.remember_chat(message.chat.id)
    await message.answer(help_message(), parse_mode="Markdown")


@router.message(Command("employees"))
async def employees_command(message: Message, finance_service: FinanceService) -> None:
    await finance_service.remember_chat(message.chat.id)
    employees = await finance_service.list_employees()
    await message.answer(employees_message(employees))


@router.message(Command("set_salary"))
async def set_salary_command(
    message: Message,
    finance_service: FinanceService,
    settings: Settings,
) -> None:
    await finance_service.remember_chat(message.chat.id)
    if settings.admin_ids and message.chat.id not in settings.admin_ids:
        await message.answer("Команда доступна только администратору.")
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await message.answer("Формат: `/set_salary Имя 2500`", parse_mode="Markdown")
        return

    _, name, salary_text = parts
    try:
        salary = int(salary_text.replace(" ", ""))
    except ValueError:
        await message.answer(
            "Ставка должна быть числом. Например: `/set_salary Настя 2000`",
            parse_mode="Markdown",
        )
        return

    if salary < 0 or salary > 1_000_000:
        await message.answer("Ставка выглядит некорректно, проверьте сумму.")
        return

    employee_name, salary_amount = await finance_service.set_salary(name, salary)
    await message.answer(f"✅ Обновил ставку: {employee_name} — {salary_amount}")


@router.message(F.text.func(lambda text: text and text.strip().lower() == "неделя"))
async def week_stats(message: Message, finance_service: FinanceService, settings: Settings) -> None:
    await finance_service.remember_chat(message.chat.id)
    period, totals = await finance_service.statistics_for_week(current_date(settings.timezone))
    salary_period, salary_totals = await finance_service.weekly_salary_breakdown(
        current_date(settings.timezone)
    )
    await message.answer(
        stats_message(period, totals) + "\n\n" + weekly_salary_message(salary_period, salary_totals)
    )


@router.message(F.text.func(lambda text: text and text.strip().lower() in {"зарплата", "зп"}))
async def salary_stats(
    message: Message,
    finance_service: FinanceService,
    settings: Settings,
) -> None:
    await finance_service.remember_chat(message.chat.id)
    period, totals = await finance_service.weekly_salary_breakdown(current_date(settings.timezone))
    await message.answer(weekly_salary_message(period, totals))


@router.message(F.text.func(lambda text: text and text.strip().lower() == "месяц"))
async def month_stats(
    message: Message,
    finance_service: FinanceService,
    settings: Settings,
) -> None:
    await finance_service.remember_chat(message.chat.id)
    period, totals = await finance_service.statistics_for_month(current_date(settings.timezone))
    await message.answer(stats_message(period, totals))


@router.message(F.text)
async def finance_text(
    message: Message,
    finance_service: FinanceService,
    settings: Settings,
) -> None:
    await finance_service.remember_chat(message.chat.id)
    text = message.text or ""
    try:
        result = await finance_service.process_text_message(
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            today=current_date(settings.timezone),
        )
        if result.duplicate:
            await message.answer(duplicate_message())
            return
        if result.parse_error:
            await message.answer(result.parse_error, parse_mode="Markdown")
            return
        if result.response_text:
            await message.answer(result.response_text)
            return
        if result.entry is None:
            await message.answer(
                "Не смог записать сообщение. Попробуйте еще раз или проверьте формат."
            )
            return
        if result.sheet_error:
            await message.answer(success_with_sheet_warning(result.entry, updated=result.updated))
            return
        if result.updated:
            await message.answer(update_message(result.entry))
            return
        await message.answer(success_message(result.entry))
    except Exception:
        logger.exception("Unhandled message processing error")
        await message.answer(
            "Не смог обработать сообщение из-за внутренней ошибки. "
            "Ошибка записана в лог, данные можно проверить повторной отправкой."
        )


@router.message()
async def fallback(message: Message, finance_service: FinanceService) -> None:
    await finance_service.remember_chat(message.chat.id)
    await message.answer(help_message(), parse_mode="Markdown")
