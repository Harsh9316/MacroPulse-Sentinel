"""
Structured JSON logging via structlog + rich console output.
"""
from __future__ import annotations

import logging
import sys

import structlog
from rich.console import Console
from rich.logging import RichHandler

from macropulse.config import settings

_console = Console(stderr=True)


def _configure_stdlib_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=_console, rich_tracebacks=True, markup=True)],
    )
    # Quieten noisy libraries
    for lib in ("httpx", "httpcore", "aiohttp", "telegram", "tweepy"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def configure_logging() -> None:
    _configure_stdlib_logging()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:  # type: ignore[type-arg]
    return structlog.get_logger(name)
