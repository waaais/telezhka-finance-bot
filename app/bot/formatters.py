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


def duplicate_message() -> str:
    return "☑️ Это сообщение уже было обработано, дубль не записываю."


def help_message() -> str:
    return (
        "Пришлите выручку свободным текстом.\n\n"
        "Примеры:\n"
        "`Ксюша нал 12500 безнал 38640`\n"
        "`2 июля Настя нал 14000 безнал 42000`\n"
        "`сегодня Кристина наличка 10 000 карта 22 500`\n"
        "`измени наличку за 2 июля на 19000`\n\n"
        "Отчеты: `неделя`, `месяц`."
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


def employees_message(employees: list[tuple[str, int]]) -> str:
    if not employees:
        return "Сотрудников пока нет."
    lines = ["👥 Сотрудники и ставки:"]
    lines.extend(f"• {name}: {money(salary)}" for name, salary in employees)
    return "\n".join(lines)
