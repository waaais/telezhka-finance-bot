import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings
from app.integrations.evotor_token_receiver import load_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvotorRevenue:
    entry_date: date
    cash: int
    cashless: int
    raw: Any

    @property
    def revenue(self) -> int:
        return self.cash + self.cashless


class EvotorSync(Protocol):
    async def fetch_revenue(self, entry_date: date) -> EvotorRevenue | None:
        pass


class DisabledEvotorSync:
    async def fetch_revenue(self, entry_date: date) -> EvotorRevenue | None:
        return None


class EvotorClient:
    def __init__(self, settings: Settings) -> None:
        self.token = settings.evotor_token
        self.token_file = settings.evotor_token_file
        self.base_url = settings.evotor_base_url.rstrip("/")
        self.revenue_url_template = settings.evotor_revenue_url_template
        self.store_uuid = settings.evotor_store_uuid
        self.terminal_uuid = settings.evotor_terminal_uuid
        self.timeout_seconds = settings.evotor_timeout_seconds
        self.auth_header_name = settings.evotor_auth_header_name

        if not self.revenue_url_template:
            raise ValueError("EVOTOR_REVENUE_URL_TEMPLATE is required when EVOTOR_ENABLED=true")

    async def fetch_revenue(self, entry_date: date) -> EvotorRevenue | None:
        return await asyncio.to_thread(self._fetch_revenue_sync, entry_date)

    def _fetch_revenue_sync(self, entry_date: date) -> EvotorRevenue:
        payload = self._request_json(entry_date)
        cash, cashless = extract_revenue(payload, terminal_uuid=self.terminal_uuid)
        return EvotorRevenue(entry_date=entry_date, cash=cash, cashless=cashless, raw=payload)

    def _request_json(self, entry_date: date) -> Any:
        url = self._build_url(entry_date)
        token = self._token()
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                self.auth_header_name: token,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Evotor API HTTP {exc.code}: {message}") from exc
        except URLError as exc:
            raise RuntimeError(f"Evotor API connection error: {exc.reason}") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Evotor API returned invalid JSON") from exc

    def _build_url(self, entry_date: date) -> str:
        date_from = datetime.combine(entry_date, time.min).isoformat()
        date_to = datetime.combine(entry_date + timedelta(days=1), time.min).isoformat()
        replacements = {
            "base_url": self.base_url,
            "date": entry_date.isoformat(),
            "date_from": date_from,
            "date_to": date_to,
            "store_uuid": self.store_uuid,
            "terminal_uuid": self.terminal_uuid,
        }
        url = self.revenue_url_template.format(**replacements)
        if url.startswith("/"):
            url = self.base_url + url
        if "{" in url or "}" in url:
            raise RuntimeError("EVOTOR_REVENUE_URL_TEMPLATE contains unknown placeholders")
        return url

    def _token(self) -> str:
        token = self.token or load_token(self.token_file)
        if not token:
            raise RuntimeError("Evotor API token is not configured yet")
        return token


def create_evotor_sync(settings: Settings) -> EvotorSync:
    if not settings.evotor_enabled:
        return DisabledEvotorSync()
    return EvotorClient(settings)


def extract_revenue(payload: Any, *, terminal_uuid: str = "") -> tuple[int, int]:
    explicit = _extract_explicit_totals(payload)
    if explicit is not None:
        return explicit

    cash = 0
    cashless = 0
    for document in _iter_documents(payload):
        if terminal_uuid and not _matches_terminal(document, terminal_uuid):
            continue
        document_cash, document_cashless = _extract_document_revenue(document)
        cash += document_cash
        cashless += document_cashless
    return cash, cashless


def _extract_explicit_totals(payload: Any) -> tuple[int, int] | None:
    if not isinstance(payload, dict):
        return None
    cash = _first_number(payload, ("cash", "cashSum", "cashAmount", "нал", "наличные"))
    cashless = _first_number(
        payload,
        ("cashless", "cashlessSum", "card", "cardSum", "cardAmount", "безнал", "безналичные"),
    )
    if cash is None and cashless is None:
        totals = payload.get("totals")
        if isinstance(totals, dict):
            return _extract_explicit_totals(totals)
        return None
    return cash or 0, cashless or 0


def _iter_documents(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "documents", "receipts", "sales", "data", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _iter_documents(value)
            if nested:
                return nested
    return [payload]


def _matches_terminal(document: dict[str, Any], terminal_uuid: str) -> bool:
    needle = terminal_uuid.casefold()
    for key in ("terminalUuid", "terminal_uuid", "deviceUuid", "device_uuid", "cashRegisterId"):
        value = document.get(key)
        if value is not None and str(value).casefold() == needle:
            return True
    return needle in json.dumps(document, ensure_ascii=False).casefold()


def _extract_document_revenue(document: dict[str, Any]) -> tuple[int, int]:
    payments = _payments(document)
    if payments:
        cash = 0
        cashless = 0
        for payment in payments:
            amount = _payment_amount(payment)
            payment_text = json.dumps(payment, ensure_ascii=False).casefold()
            if _looks_cashless(payment_text):
                cashless += amount
            elif _looks_cash(payment_text):
                cash += amount
            else:
                cashless += amount
        return cash, cashless

    amount = _first_number(document, ("sum", "total", "amount", "revenue", "price")) or 0
    payment_text = json.dumps(document, ensure_ascii=False).casefold()
    if _looks_cash(payment_text):
        return amount, 0
    return 0, amount


def _payments(document: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("payments", "payment", "paymentItems", "payment_items"):
        value = document.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
    return []


def _payment_amount(payment: dict[str, Any]) -> int:
    return _first_number(payment, ("amount", "sum", "total", "value", "paid")) or 0


def _looks_cashless(value: str) -> bool:
    return any(marker in value for marker in ("cashless", "card", "bank", "безнал", "карта"))


def _looks_cash(value: str) -> bool:
    return any(marker in value for marker in ("cash", "налич", "нал "))


def _first_number(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    lowered = {str(key).casefold(): value for key, value in payload.items()}
    for key in keys:
        value = lowered.get(key.casefold())
        number = _number(value)
        if number is not None:
            return number
    return None


def _number(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return int(value)
    normalized = (
        str(value)
        .replace("\u00a0", "")
        .replace(" ", "")
        .replace("₽", "")
        .replace(",", ".")
        .strip()
    )
    try:
        return int(float(normalized))
    except ValueError:
        return None
