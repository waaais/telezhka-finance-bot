import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from app.config import Settings

logger = logging.getLogger(__name__)

TOKEN_KEYS = {
    "token",
    "access_token",
    "accesstoken",
    "app_token",
    "apptoken",
    "evotor_token",
    "evotortoken",
}
SECRET_KEYS = {
    "secret",
    "auth",
    "auth_token",
    "authtoken",
    "verification_token",
    "verificationtoken",
    "callback_token",
    "callbacktoken",
}
RECEIPT_KEYS = {
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
    "type",
}


async def start_evotor_token_receiver(settings: Settings) -> web.AppRunner | None:
    if not settings.evotor_token_receiver_enabled and not settings.evotor_receipts_enabled:
        logger.info("Evotor web receiver is disabled")
        return None
    if not settings.evotor_callback_secret:
        raise RuntimeError("EVOTOR_CALLBACK_SECRET is required when Evotor receiver is enabled")

    app = web.Application()
    app["settings"] = settings
    callback_path = _normalize_path(settings.evotor_callback_path)
    receipts_path = _normalize_path(settings.evotor_receipts_path)
    app.router.add_get("/health", healthcheck)
    if settings.evotor_token_receiver_enabled:
        app.router.add_get(callback_path, receive_evotor_token)
        app.router.add_post(callback_path, receive_evotor_token)
    if settings.evotor_receipts_enabled:
        app.router.add_get(receipts_path, receive_evotor_receipt)
        app.router.add_post(receipts_path, receive_evotor_receipt)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.evotor_callback_host, settings.evotor_callback_port)
    await site.start()
    logger.info(
        "Evotor web receiver started",
        extra={
            "host": settings.evotor_callback_host,
            "port": settings.evotor_callback_port,
            "token_path": callback_path if settings.evotor_token_receiver_enabled else "",
            "receipts_path": receipts_path if settings.evotor_receipts_enabled else "",
        },
    )
    return runner


async def healthcheck(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def receive_evotor_token(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    data = await _request_data(request)
    if not _is_authorized(request, data, settings.evotor_callback_secret):
        logger.warning("Rejected Evotor token callback with invalid secret")
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    token = extract_token(data)
    if not token:
        if settings.evotor_receipts_enabled and looks_like_receipt_payload(data):
            save_receipt(settings.evotor_receipts_file, data)
            logger.info("Evotor receipt saved from token callback path")
            return web.json_response({"ok": True, "kind": "receipt"})
        logger.warning("Evotor token callback does not contain token")
        return web.json_response({"ok": False, "error": "token_not_found"}, status=400)

    save_token(settings.evotor_token_file, token)
    logger.info("Evotor token saved")
    return web.json_response({"ok": True})


async def receive_evotor_receipt(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    data = await _request_data(request)
    if not _is_authorized(request, data, settings.evotor_callback_secret):
        logger.warning("Rejected Evotor receipt callback with invalid secret")
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    save_receipt(settings.evotor_receipts_file, data)
    logger.info("Evotor receipt saved")
    return web.json_response({"ok": True})


def save_token(token_file: str, token: str) -> None:
    path = Path(token_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "token": token,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_receipt(receipts_file: str, payload: Any) -> None:
    path = Path(receipts_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": _remove_secret_fields(payload),
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))
        file.write("\n")


def load_receipts(receipts_file: str) -> list[dict[str, Any]]:
    path = Path(receipts_file)
    if not path.exists():
        return []
    receipts: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if isinstance(payload, dict):
                    receipts.append(payload)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read Evotor receipts file", extra={"receipts_file": receipts_file})
        return []
    return receipts


def load_token(token_file: str) -> str:
    path = Path(token_file)
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read Evotor token file", extra={"token_file": token_file})
        return ""
    token = payload.get("token")
    return str(token).strip() if token else ""


def extract_token(data: dict[str, Any]) -> str:
    value = _find_value_by_keys(data, TOKEN_KEYS)
    return str(value).strip() if value else ""


def _is_authorized(request: web.Request, data: dict[str, Any], expected_secret: str) -> bool:
    expected = expected_secret.strip()
    if not expected:
        return False

    candidates = [
        request.headers.get("X-Authorization", ""),
        request.headers.get("X-Evotor-Authorization", ""),
        request.headers.get("X-Evotor-Token", ""),
        request.headers.get("Authorization", "").removeprefix("Bearer ").strip(),
        str(_find_value_by_keys(data, SECRET_KEYS | TOKEN_KEYS) or ""),
    ]
    return any(candidate.strip() == expected for candidate in candidates)


async def _request_data(request: web.Request) -> dict[str, Any]:
    data: dict[str, Any] = dict(request.query)
    if request.can_read_body:
        content_type = request.content_type.lower()
        if content_type == "application/json":
            try:
                body = await request.json()
            except json.JSONDecodeError:
                body = {}
            if isinstance(body, dict):
                data.update(body)
            elif isinstance(body, list):
                data["items"] = body
        else:
            form = await request.post()
            data.update({key: value for key, value in form.items()})
    return data


def _remove_secret_fields(data: Any) -> Any:
    if isinstance(data, dict):
        clean: dict[str, Any] = {}
        for key, value in data.items():
            normalized_key = str(key).replace("-", "_").casefold()
            if normalized_key in SECRET_KEYS | TOKEN_KEYS:
                continue
            clean[key] = _remove_secret_fields(value)
        return clean
    if isinstance(data, list):
        return [_remove_secret_fields(item) for item in data]
    return data


def _find_value_by_keys(data: Any, keys: set[str]) -> Any:
    if isinstance(data, dict):
        for key, value in data.items():
            normalized_key = str(key).replace("-", "_").casefold()
            if normalized_key in keys and value:
                return value
        for value in data.values():
            nested = _find_value_by_keys(value, keys)
            if nested:
                return nested
    if isinstance(data, list):
        for item in data:
            nested = _find_value_by_keys(item, keys)
            if nested:
                return nested
    return None


def looks_like_receipt_payload(data: Any) -> bool:
    if isinstance(data, dict):
        normalized_keys = {str(key).replace("-", "_").casefold() for key in data}
        if normalized_keys & RECEIPT_KEYS:
            return True
        return any(looks_like_receipt_payload(value) for value in data.values())
    if isinstance(data, list):
        return any(looks_like_receipt_payload(item) for item in data)
    return False


def _normalize_path(value: str) -> str:
    path = value.strip() or "/evotor/token"
    return path if path.startswith("/") else f"/{path}"
