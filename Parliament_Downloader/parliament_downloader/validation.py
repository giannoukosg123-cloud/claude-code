"""Local file validation: existence, size, PDF signature, parseability,
page count, hash. Self-contained -- not shared with skrip_downloader.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from . import config


@dataclass
class ValidationResult:
    ok: bool
    size_bytes: int = 0
    sha256: str = ""
    page_count: int = 0
    error: Optional[str] = None


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_pdf_file(path: Path) -> ValidationResult:
    if not path.exists():
        return ValidationResult(ok=False, error="file_does_not_exist")

    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        return ValidationResult(ok=False, size_bytes=size_bytes, error="empty_file")

    with open(path, "rb") as f:
        header = f.read(len(config.PDF_MAGIC))
    if header != config.PDF_MAGIC:
        return ValidationResult(ok=False, size_bytes=size_bytes, error=f"bad_magic_bytes:{header!r}")

    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
    except (PdfReadError, Exception) as exc:  # noqa: BLE001 - any parser failure is a real failure
        return ValidationResult(ok=False, size_bytes=size_bytes, error=f"pdf_parse_error:{exc}")

    if page_count < 1:
        return ValidationResult(ok=False, size_bytes=size_bytes, error="zero_pages")

    digest = sha256_of_file(path)
    return ValidationResult(ok=True, size_bytes=size_bytes, sha256=digest, page_count=page_count)
