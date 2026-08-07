"""Assembling an issue's validated pages into a single merged PDF.

Reads exclusively from the issue's manifest.json page list (in
page_number order) — never a directory glob — so a stray file from
another issue can never sneak into the merge.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from pypdf import PdfReader, PdfWriter

from . import config
from .manifest import pages_dir
from .validation import sha256_of_file


class MergeError(RuntimeError):
    pass


def merged_pdf_path(year: int, issue_id: str) -> Path:
    return config.ISSUES_ROOT / str(year) / f"{issue_id}.pdf"


def merge_issue(year: int, issue_id: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
    pages = sorted(manifest["pages"], key=lambda p: p["page_number"])

    ok_pages = [p for p in pages if p["status"] == "ok"]
    if len(ok_pages) != len(pages) or len(ok_pages) != manifest["pages_expected"]:
        raise MergeError(
            f"Refusing to merge {issue_id}: not all pages are ok "
            f"({len(ok_pages)}/{manifest['pages_expected']})"
        )

    expected_numbers = list(range(1, len(pages) + 1))
    actual_numbers = [p["page_number"] for p in pages]
    if actual_numbers != expected_numbers:
        raise MergeError(
            f"Refusing to merge {issue_id}: page numbers not sequential from 1: {actual_numbers}"
        )

    p_dir = pages_dir(year, issue_id)
    writer = PdfWriter()
    for page in pages:
        local_path = p_dir / page["local_filename"]
        if not local_path.exists():
            raise MergeError(f"Manifest references missing file: {local_path}")
        reader = PdfReader(str(local_path))
        for pdf_page in reader.pages:
            writer.add_page(pdf_page)

    out_path = merged_pdf_path(year, issue_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(out_path.parent), prefix=".merge_", suffix=".pdf.tmp")
    os.close(fd)
    with open(tmp_path, "wb") as f:
        writer.write(f)
    os.replace(tmp_path, out_path)

    reader_check = PdfReader(str(out_path))
    merged_page_count = len(reader_check.pages)
    if merged_page_count != len(pages):
        raise MergeError(
            f"Merged page count mismatch for {issue_id}: got {merged_page_count}, expected {len(pages)}"
        )

    return {
        "path": str(out_path),
        "page_count": merged_page_count,
        "bytes": out_path.stat().st_size,
        "sha256": sha256_of_file(out_path),
        "status": "ok",
    }
