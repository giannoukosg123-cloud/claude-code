"""Per-issue manifest.json read/write helpers.

The manifest is the single source of truth for what pages belong to an
issue and in what order. Merging NEVER globs a directory; it always reads
this file.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config


def issue_dir(year: int, issue_id: str) -> Path:
    return config.RAW_ROOT / str(year) / issue_id


def pages_dir(year: int, issue_id: str) -> Path:
    return issue_dir(year, issue_id) / "pages"


def manifest_path(year: int, issue_id: str) -> Path:
    return issue_dir(year, issue_id) / "manifest.json"


def new_manifest(
    *,
    issue_date: str,
    issue_id: str,
    wrapper_url: str,
    pages_url: str,
    pages_expected: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """pages_expected: list of {"page_number": int, "page_id": int}."""
    return {
        "newspaper": config.NEWSPAPER_NAME_GR,
        "newspaper_code": config.NEWSPAPER_CODE,
        "newspaper_id": config.NEWSPAPER_ID,
        "issue_date": issue_date,
        "issue_id": issue_id,
        "wrapper_url": wrapper_url,
        "pages_url": pages_url,
        "status": "pending",
        "pages_expected": len(pages_expected),
        "pages_downloaded": 0,
        "pages": [
            {
                "page_number": p["page_number"],
                "page_id": p["page_id"],
                "showpdf_url": None,
                "redirect_location": None,
                "resolved_pdf_url": None,
                "original_filename": None,
                "local_filename": None,
                "bytes": None,
                "sha256": None,
                "status": "pending",
                "error": None,
            }
            for p in pages_expected
        ],
        "merged_pdf": None,
    }


def save_manifest(year: int, issue_id: str, data: Dict[str, Any]) -> None:
    data["pages"] = sorted(data["pages"], key=lambda p: p["page_number"])
    path = manifest_path(year, issue_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".manifest_", suffix=".json.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_manifest(year: int, issue_id: str) -> Optional[Dict[str, Any]]:
    path = manifest_path(year, issue_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
