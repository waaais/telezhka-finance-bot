import logging
import sys

from pythonjsonlogger import jsonlogger


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s %(exc_info)s"
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)

