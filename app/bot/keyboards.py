from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="неделя"),
                KeyboardButton(text="месяц"),
            ],
            [
                KeyboardButton(text="зарплата"),
                KeyboardButton(text="эвотор"),
            ],
            [
                KeyboardButton(text="помощь"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Ксюша нал 3000 безнал 5000",
    )
