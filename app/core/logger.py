from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from app.core.config import settings

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

def formatter(record) -> str:
    req_id = record["extra"].get("request_id", "-")
    extra_data = {k: v for k, v in record["extra"].items() if k not in ("request_id", "tag")}
    extra_str = ""
    if extra_data:
        # Replace { } with [ ] so Python's formatter doesn't parse them as template keys
        extra_str = f" | {extra_data}".replace("{", "[").replace("}", "]")
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        f"{req_id: <16} | "
        "<level>{message}</level>"
        f"{extra_str}\n"
    )


def setup_logging() -> None:
    logger.remove()

    logger.add(
        sys.stderr,
        level=settings.log_level.upper(),
        format=formatter,
        colorize=True,
    )

    logger.add(
        LOG_DIR / "application.log",
        level="DEBUG",
        format=formatter,
        rotation="10 MB",
        retention="30 days",
        serialize=False,
    )

    logger.add(
        LOG_DIR / "error.log",
        level="ERROR",
        format=formatter,
        rotation="10 MB",
        retention="90 days",
        backtrace=True,
        diagnose=True,
    )

    logger.add(
        LOG_DIR / "oauth.log",
        level="DEBUG",
        format=formatter,
        rotation="10 MB",
        retention="30 days",
        filter=lambda record: "oauth" in record["extra"].get("tag", ""),
    )

    logger.add(
        LOG_DIR / "linkedin.log",
        level="DEBUG",
        format=formatter,
        rotation="10 MB",
        retention="30 days",
        filter=lambda record: "linkedin" in record["extra"].get("tag", ""),
    )

    logger.add(
        LOG_DIR / "scheduler.log",
        level="DEBUG",
        format=formatter,
        rotation="10 MB",
        retention="30 days",
        filter=lambda record: "scheduler" in record["extra"].get("tag", ""),
    )

    logger.add(
        LOG_DIR / "database.log",
        level="DEBUG",
        format=formatter,
        rotation="10 MB",
        retention="30 days",
        filter=lambda record: "database" in record["extra"].get("tag", ""),
    )


def get_logger(**kwargs: str) -> logger:  # type: ignore[no-untyped-def]
    return logger.bind(**kwargs)  # type: ignore[no-any-return]
