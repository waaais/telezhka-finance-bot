import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings
from app.integrations.evotor_token_receiver import load_receipts, load_token

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


class EvotorReceiptFileSync:
    def __init__(self, settings: Settings) -> None:
        self.receipts_file = settings.evotor_receipts_file
        self.terminal_uuid = settings.evotor_terminal_uuid

    async def fetch_revenue(self, entry_date: date) -> EvotorRevenue | None:
        return await asyncio.to_thread(self._fetch_revenue_sync, entry_date)

    def _fetch_revenue_sync(self, entry_date: date) -> EvotorRevenue:
        documents = []
        for envelope in load_receipts(self.receipts_file):
            payload = envelope.get("payload", envelope)
            received_at = envelope.get("received_at", "")
            for document in _iter_documents(payload):
                if self.terminal_uuid and not _matches_terminal(document, self.terminal_uuid):
                    continue
                document_date = _document_date(document, fallback=received_at)
                if document_date != entry_date:
                    continue
                documents.append(document)
        cash, cashless = extract_revenue(documents, terminal_uuid=self.terminal_uuid)
        return EvotorRevenue(entry_date=entry_date, cash=cash, cashless=cashless, raw=documents)


def create_evotor_sync(settings: Settings) -> EvotorSync:
    if not settings.evotor_enabled:
        return DisabledEvotorSync()
    if settings.evotor_revenue_url_template:
        return EvotorClient(settings)
    if settings.evotor_receipts_enabled:
        return EvotorReceiptFileSync(settings)
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
    if _is_document_like(payload):
        return [payload]
    for key in ("documents", "receipts", "sales", "data", "result", "items"):
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
    for key in (
        "terminalUuid",
        "terminal_uuid",
        "terminalId",
        "terminal_id",
        "deviceUuid",
        "device_uuid",
        "deviceId",
        "device_id",
        "cashRegisterId",
        "cash_register_id",
        "kkm",
    ):
        value = document.get(key)
        if value is not None and str(value).casefold() == needle:
            return True
    return needle in json.dumps(document, ensure_ascii=False).casefold()


def _extract_document_revenue(document: dict[str, Any]) -> tuple[int, int]:
    sign = _document_sign(document)
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
        return cash * sign, cashless * sign

    amount = _first_number(
        document,
        ("sum", "total", "totalAmount", "total_amount", "amount", "revenue", "price"),
    ) or 0
    payment_text = json.dumps(document, ensure_ascii=False).casefold()
    if _looks_cash(payment_text):
        return amount * sign, 0
    return 0, amount * sign


def _payments(document: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("payments", "payment", "paymentItems", "payment_items"):
        value = document.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
    return []


def _payment_amount(payment: dict[str, Any]) -> int:
    return _first_number(payment, ("amount", "sum", "total", "totalAmount", "value", "paid")) or 0


def _looks_cashless(value: str) -> bool:
    return any(marker in value for marker in ("cashless", "card", "bank", "electronic", "безнал", "карта"))


def _looks_cash(value: str) -> bool:
    return any(marker in value for marker in ("cash", "налич", "нал "))


def _is_document_like(payload: dict[str, Any]) -> bool:
    keys = {str(key).casefold() for key in payload}
    return bool(
        keys
        & {
            "dateTime".casefold(),
            "datetime",
            "date",
            "createdAt".casefold(),
            "totalAmount".casefold(),
            "total_amount",
            "paymentSource".casefold(),
            "payments",
            "payment",
            "deviceId".casefold(),
            "device_id",
        }
    )


def _document_date(document: dict[str, Any], *, fallback: Any = "") -> date | None:
    value = _first_value(
        document,
        (
            "dateTime",
            "datetime",
            "date",
            "createdAt",
            "created_at",
            "createdDate",
            "created_date",
            "time",
            "timestamp",
        ),
    )
    parsed = _parse_date(value)
    if parsed is not None:
        return parsed
    return _parse_date(fallback)


def _document_sign(document: dict[str, Any]) -> int:
    value = str(_first_value(document, ("type", "operationType", "operation_type")) or "")
    value = value.casefold()
    if any(marker in value for marker in ("payback", "return", "refund", "возврат")):
        return -1
    return 1


def _first_number(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    lowered = {str(key).casefold(): value for key, value in payload.items()}
    for key in keys:
        value = lowered.get(key.casefold())
        number = _number(value)
        if number is not None:
            return number
    return None


def _first_value(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = {str(key).casefold(): value for key, value in payload.items()}
    for key in keys:
        value = lowered.get(key.casefold())
        if value is not None:
            return value
    return None


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp).date()
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
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
