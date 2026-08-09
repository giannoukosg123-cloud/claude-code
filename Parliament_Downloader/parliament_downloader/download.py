"""Downloading a single scan. main.asp?current=ID returns the PDF directly
(confirmed -- no redirect-resolution dance needed here, unlike SKRIP's
showpdf.asp)."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import requests

from . import config
from .discovery import main_url
from .http_client import get_with_retry


class DownloadError(RuntimeError):
    pass


def download_scan_bytes(
    session: requests.Session,
    current_id: int,
    dest_path: Path,
    logger: logging.Logger,
    referer: str,
) -> tuple[int, str]:
    """Download main.asp?current=current_id to dest_path atomically.
    Returns (bytes_written, content_type)."""
    url = main_url(current_id)
    response = get_with_retry(session, url, logger=logger, log_label="main.asp", stream=True, referer=referer)

    if response.status_code != 200:
        raise DownloadError(f"Unexpected status {response.status_code} downloading {url}")

    content_type = response.headers.get("content-type", "")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(dest_path.parent), prefix=".dl_", suffix=".part")
    bytes_written = 0
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    bytes_written += len(chunk)
        os.replace(tmp_path, dest_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return bytes_written, content_type
