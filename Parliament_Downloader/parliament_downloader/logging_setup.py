"""Logging helpers. Independent from skrip_downloader's (deliberately not
shared/imported) but the same spirit: every scan-relevant log line carries
enough fields to reconstruct exactly what happened without guessing.

Each Record gets its own logger instance (keyed by item/seg), so two
different segments processed in the same process can never end up writing
into each other's log file -- logging.getLogger() returns the SAME
singleton for a given name, so a shared/fixed logger name would have
leaked one segment's log lines into another's file the moment a second
record was ever used."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import config


def setup_logger(record: config.Record) -> logging.Logger:
    # record.log_path.parent is LOG_ROOT for a standard record, but the
    # record's own custom_root for one that overrides it (e.g. a Desktop
    # folder) -- that directory may not exist yet either.
    record.log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"parliament_downloader.item{record.item}.seg{record.seg}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter("%(message)s")

        file_handler = logging.FileHandler(record.log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def log_event(
    logger: logging.Logger,
    record: config.Record,
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
        f"item={record.item}",
        f"seg={record.seg}",
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
