"""Logging helpers. Independent from skrip_downloader's (deliberately not
shared/imported) but the same spirit: every scan-relevant log line carries
enough fields to reconstruct exactly what happened without guessing."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import config


def setup_logger() -> logging.Logger:
    config.LOG_ROOT.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("parliament_downloader")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter("%(message)s")

        file_handler = logging.FileHandler(config.LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def log_event(
    logger: logging.Logger,
    *,
    logical_position: Optional[int],
    current_id: Optional[int],
    action: str,
    source_url: Optional[str],
    local_path: Optional[str],
    status: str,
    extra: str = "",
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    fields = [
        ts,
        f"item={config.ITEM}",
        f"seg={config.SEG}",
        f"pos={logical_position if logical_position is not None else '-'}",
        f"current={current_id if current_id is not None else '-'}",
        action,
        str(source_url) if source_url else "-",
        str(local_path) if local_path else "-",
        status,
    ]
    line = " | ".join(fields)
    if extra:
        line += f" | {extra}"
    logger.info(line)
