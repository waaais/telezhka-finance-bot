from datetime import date

from app.statistics.engine import Period
from app.storage.models import FinanceEntry


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def success_message(entry: FinanceEntry) -> str:
    return (
        "✅ Записал\n\n"
        + entry_details(entry)
    )


def update_message(entry: FinanceEntry) -> str:
    return (
        "✅ Обновил\n\n"
        + entry_details(entry)
    )


def entry_details(entry: FinanceEntry) -> str:
    return (
        f"📅 {entry.entry_date:%d.%m.%Y}\n"
        f"👤 {entry.employee_name}\n"
        f"💵 наличка: {money(entry.cash)}\n"
        f"💳 безнал: {money(entry.cashless)}\n"
        f"💰 выручка: {money(entry.revenue)}\n"
        f"👷 зарплата: {money(entry.salary)}"
    )


def success_with_sheet_warning(entry: FinanceEntry, *, updated: bool = False) -> str:
    base_message = update_message(entry) if updated else success_message(entry)
    action = "обновлена" if updated else "сохранена"
    return (
        base_message
        + f"\n\n⚠️ В базе запись {action}, но в Google Sheets не отправилась. "
        "Ошибка записана в лог."
    )


def daily_evotor_summary(
    entry: FinanceEntry,
    *,
    updated: bool = False,
    sheet_error: bool = False,
) -> str:
    action = "обновил" if updated else "записал"
    text = (
        "📊 Сводка за сегодня\n\n"
        f"📅 {entry.entry_date:%d.%m.%Y}\n"
        f"👤 работали: {entry.employee_name}\n"
        f"💵 наличка: {money(entry.cash)}\n"
        f"💳 безнал: {money(entry.cashless)}\n"
        f"💰 выручка: {money(entry.revenue)}\n"
        f"👷 зарплата: {money(entry.salary)}\n\n"
        f"✅ Evotor посчитал чеки, я {action} день в таблицу."
    )
    if sheet_error:
        text += "\n\n⚠️ В базе сохранил, но в Google Sheets не отправилось. Ошибка записана в лог."
    return text


def daily_staff_reminder_message(
    day: date,
    employee_name: str | None,
    *,
    sheet_error: bool = False,
) -> str:
    if sheet_error:
        return (
            "👤 Кто работает сегодня\n\n"
            f"📅 {day:%d.%m.%Y}\n"
            "⚠️ Не смог прочитать расписание в Google Sheets."
        )
    if not employee_name:
        return (
            "👤 Кто работает сегодня\n\n"
            f"📅 {day:%d.%m.%Y}\n"
            "⚠️ В расписании на сегодня сотрудник не указан."
        )
    return (
        "👤 Кто работает сегодня\n\n"
        f"📅 {day:%d.%m.%Y}\n"
        f"👷 {employee_name}"
    )


def duplicate_message() -> str:
    return "☑️ Это сообщение уже было обработано, дубль не записываю."


def help_message() -> str:
    return (
        "*Выручка*\n"
        "`Ксюша нал 12500 безнал 38640`\n"
        "`Ксюша+Дима нал 12500 безнал 38640`\n"
        "`2 июля Настя нал 14000 безнал 42000`\n"
        "`сегодня нал 13000 безнал 28000` — продавца возьму из расписания.\n\n"
        "*Даты*\n"
        "Понимаю: `сегодня`, `вчера`, `завтра`, `послезавтра`, `03.07`, "
        "`2 июля`, `за пятницу`. Без даты — сегодня.\n\n"
        "*Исправления*\n"
        "`измени наличку за 2 июля на 19000`\n"
        "`измени безнал 02.07 на 42000`\n"
        "`измени продавца сегодня на Дима`\n\n"
        "*Не работаем*\n"
        "`сегодня не работаем`\n"
        "`завтра не работаем`\n"
        "`12.08 не работаем`\n"
        "Поставлю нал, безнал и зарплату 0; продавца возьму из расписания.\n\n"
        "*Расписание*\n"
        "Пришлите списком, по одной строке на день:\n"
        "`пн. 13.07 — Ксюша`\n"
        "`вт. 14.07 — Настя+Дима`\n\n"
        "*Отчёты и настройки*\n"
        "`неделя` — недельный отчёт\n"
        "`месяц` — месячный отчёт\n"
        "`зарплата` или `зп` — зарплата за неделю\n"
        "`/employees` — сотрудники и ставки\n"
        "`/set_salary Дима 2500` — изменить ставку\n"
        "`эвотор` — импорт из кассы (сейчас выключен)\n"
        "`/help` — показать эту памятку"
    )


def stats_message(period: Period, totals: dict[str, int]) -> str:
    return (
        f"📊 Статистика за {period.name}\n"
        f"📅 {period.start:%d.%m.%Y} — {period.end:%d.%m.%Y}\n\n"
        f"💵 общий нал: {money(totals['cash'])}\n"
        f"💳 общий безнал: {money(totals['cashless'])}\n"
        f"💰 общая выручка: {money(totals['revenue'])}\n"
        f"👷 сумма зарплат: {money(totals['salaries'])}\n"
        f"📈 чистая прибыль: {money(totals['profit'])}\n"
        f"🧾 записей: {totals['entries']}"
    )


def weekly_salary_message(period: Period, totals: dict[str, int]) -> str:
    return (
        f"👷 Зарплата за неделю\n"
        f"📅 {period.start:%d.%m.%Y} — {period.end:%d.%m.%Y}\n\n"
        f"Ксюша: {money(totals.get('КСЮША', 0))}\n"
        f"Настя: {money(totals.get('НАСТЯ', 0))}\n"
        f"Кристина: {money(totals.get('КРИСТИНА', 0))}\n"
        f"& остальные: {money(totals.get('&', 0))}"
    )


def employees_message(employees: list[tuple[str, int]]) -> str:
    if not employees:
        return "Сотрудников пока нет."
    lines = ["👥 Сотрудники и ставки:"]
    lines.extend(f"• {name}: {money(salary)}" for name, salary in employees)
    return "\n".join(lines)
