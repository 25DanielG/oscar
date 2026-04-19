from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

_SHARED: list[Any] = [
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="%H:%M:%S"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]

def configure(level: str = "INFO", log_dir: Path | None = None) -> None:
    structlog.configure(
        processors=_SHARED + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())],
            foreign_pre_chain=_SHARED,
        )
    )

    handlers: list[logging.Handler] = [console_handler]

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            log_dir / "oscar.log",
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[structlog.processors.JSONRenderer()],
                foreign_pre_chain=_SHARED,
            )
        )
        handlers.append(file_handler)

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper()),
        handlers=handlers,
        force=True,
    )
