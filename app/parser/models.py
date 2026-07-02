from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ParsedFinanceMessage:
    employee_name: str
    entry_date: date
    cash: int
    cashless: int
    raw_text: str

    @property
    def revenue(self) -> int:
        return self.cash + self.cashless


@dataclass(frozen=True)
class ParsedFinanceCorrection:
    entry_date: date
    employee_name: str | None
    cash: int | None
    cashless: int | None
    raw_text: str


class ParseError(ValueError):
    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message
