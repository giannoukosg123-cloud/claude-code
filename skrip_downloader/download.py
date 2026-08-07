"""Resolving a page_id to a real PDF URL, and downloading the bytes.

showpdf.asp?c=123&p={page_id} returns an HTTP redirect whose Location
header points (with backslashes instead of slashes, e.g.
"..\\output\\123_39360_-1.pdf") at the real file under /output/.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse, unquote

import requests

from . import config
from .context import DownloadContext
from .http_client import get_with_retry
from .logging_setup import log_event


class ResolveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedPdf:
    showpdf_url: str
    redirect_location: str
    resolved_pdf_url: str
    original_filename: str


def _showpdf_url(page_id: int) -> str:
    return f"{config.BASE_URL}{config.SHOWPDF_PATH}?c={config.NEWSPAPER_ID}&p={page_id}"


def resolve_pdf_url(
    session: requests.Session,
    page_id: int,
    ctx: DownloadContext,
    logger: logging.Logger,
    referer: str,
) -> ResolvedPdf:
    showpdf_url = _showpdf_url(page_id)

    response = get_with_retry(
        session,
        showpdf_url,
        logger=logger,
        log_label="showpdf.asp",
        allow_redirects=False,
        referer=referer,
    )

    log_event(
        logger,
        issue_id=ctx.issue_id,
        issue_date=ctx.issue_date,
        page_number=ctx.page_number,
        action="SHOWPDF_RESPONSE",
        source_url=showpdf_url,
        local_path=None,
        status=str(response.status_code),
    )

    if response.status_code not in (301, 302, 303, 307, 308):
        raise ResolveError(
            f"showpdf.asp did not redirect (status={response.status_code}) for page_id={page_id}"
        )

    location = response.headers.get("Location")
    if not location:
        raise ResolveError(f"showpdf.asp redirect had no Location header for page_id={page_id}")

    normalized = unquote(location).replace("\\", "/")
    resolved = urljoin(f"{config.BASE_URL}{config.SHOWPDF_PATH}", normalized)

    original_filename = os.path.basename(urlparse(resolved).path)

    return ResolvedPdf(
        showpdf_url=showpdf_url,
        redirect_location=location,
        resolved_pdf_url=resolved,
        original_filename=original_filename,
    )


def download_pdf_bytes(
    session: requests.Session,
    resolved: ResolvedPdf,
    dest_path,
    ctx: DownloadContext,
    logger: logging.Logger,
    referer: str,
) -> int:
    """Download the PDF to dest_path atomically. Returns bytes written."""
    response = get_with_retry(
        session,
        resolved.resolved_pdf_url,
        logger=logger,
        log_label="download_pdf",
        allow_redirects=True,
        stream=True,
        referer=referer,
    )

    if response.status_code != 200:
        raise ResolveError(
            f"Unexpected status {response.status_code} downloading {resolved.resolved_pdf_url}"
        )

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

    return bytes_written
