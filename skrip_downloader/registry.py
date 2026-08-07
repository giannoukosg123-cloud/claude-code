"""Year-level issue registry: one entry per checked date."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from . import config


def registry_path(year: int) -> Path:
    return config.RAW_ROOT / str(year) / "issues_registry.json"


def load_registry(year: int) -> Dict[str, Any]:
    path = registry_path(year)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_registry(year: int, data: Dict[str, Any]) -> None:
    path = registry_path(year)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".registry_", suffix=".json.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def update_registry_entry(
    year: int,
    date_str: str,
    *,
    issue_id: str,
    status: str,
    pages_expected: int,
    pages_downloaded: int,
    merged_pdf_path: Optional[str],
    error: Optional[str],
) -> None:
    data = load_registry(year)
    data[date_str] = {
        "date": date_str,
        "issue_id": issue_id,
        "status": status,
        "pages_expected": pages_expected,
        "pages_downloaded": pages_downloaded,
        "merged_pdf_path": merged_pdf_path,
        "error": error,
    }
    save_registry(year, data)
