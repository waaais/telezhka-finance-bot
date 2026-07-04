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


async def start_evotor_token_receiver(settings: Settings) -> web.AppRunner | None:
    if not settings.evotor_token_receiver_enabled:
        logger.info("Evotor token receiver is disabled")
        return None
    if not settings.evotor_callback_secret:
        raise RuntimeError("EVOTOR_CALLBACK_SECRET is required when token receiver is enabled")

    app = web.Application()
    app["settings"] = settings
    callback_path = _normalize_path(settings.evotor_callback_path)
    app.router.add_get("/health", healthcheck)
    app.router.add_get(callback_path, receive_evotor_token)
    app.router.add_post(callback_path, receive_evotor_token)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.evotor_callback_host, settings.evotor_callback_port)
    await site.start()
    logger.info(
        "Evotor token receiver started",
        extra={
            "host": settings.evotor_callback_host,
            "port": settings.evotor_callback_port,
            "path": callback_path,
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
        logger.warning("Evotor token callback does not contain token")
        return web.json_response({"ok": False, "error": "token_not_found"}, status=400)

    save_token(settings.evotor_token_file, token)
    logger.info("Evotor token saved")
    return web.json_response({"ok": True})


def save_token(token_file: str, token: str) -> None:
    path = Path(token_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "token": token,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
        str(_find_value_by_keys(data, SECRET_KEYS) or ""),
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
        else:
            form = await request.post()
            data.update({key: value for key, value in form.items()})
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


def _normalize_path(value: str) -> str:
    path = value.strip() or "/evotor/token"
    return path if path.startswith("/") else f"/{path}"
