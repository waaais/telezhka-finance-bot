import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError

T = TypeVar("T")


async def retry_db(operation: Callable[[], Awaitable[T]], *, attempts: int = 3) -> T:
    delay = 0.2
    last_error: OperationalError | None = None
    for attempt in range(attempts):
        try:
            return await operation()
        except OperationalError as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            await asyncio.sleep(delay)
            delay *= 2
    if last_error is None:
        raise RuntimeError("Retry finished without result or error")
    raise last_error

