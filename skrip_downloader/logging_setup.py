"""Logging helpers.

Every PDF-relevant log line contains at least: timestamp, issue_id,
issue_date, page_number, action, source_url, local_path, status.
"""

from __future__ import annotations

import logging
import pathlib
from datetime import datetime, timezone
from typing import Optional

from . import config


def setup_logger(year: int) -> logging.Logger:
    config.LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = config.LOG_ROOT / f"{year}.log"

    logger = logging.getLogger(f"skrip.{year}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter("%(message)s")

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def log_event(
    logger: logging.Logger,
    *,
    issue_id: Optional[str],
    issue_date: Optional[str],
    page_number: Optional[int],
    action: str,
    source_url: Optional[str],
    local_path: Optional[str],
    status: str,
    extra: str = "",
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    page_field = f"page={page_number}" if page_number is not None else "page=-"
    fields = [
        ts,
        issue_id or "-",
        f"date={issue_date or '-'}",
        page_field,
        action,
        str(source_url) if source_url else "-",
        str(local_path) if local_path else "-",
        status,
    ]
    line = " | ".join(fields)
    if extra:
        line += f" | {extra}"
    logger.info(line)
