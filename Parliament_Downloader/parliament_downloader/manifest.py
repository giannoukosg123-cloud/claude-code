"""manifest.json read/write for this one item/segment record."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

from . import config


def new_manifest(discovered_scans: int) -> Dict[str, Any]:
    return {
        "item_id": config.ITEM,
        "segment_id": config.SEG,
        "title": config.TITLE,
        "segment_label": config.SEGMENT_LABEL,
        "expected_scans": config.EXPECTED_SCANS,
        "discovered_scans": discovered_scans,
        "scans": [],
    }


def scan_filename(logical_position: int, current_id: int) -> str:
    return f"AKROPOLIS_item{config.ITEM}_seg{config.SEG}_scan_{logical_position:04d}_current_{current_id}.pdf"


def new_scan_entry(logical_position: int, current_id: int, source_url: str) -> Dict[str, Any]:
    return {
        "logical_position": logical_position,
        "current_id": current_id,
        "source_url": source_url,
        "local_filename": scan_filename(logical_position, current_id),
        "bytes": None,
        "sha256": None,
        "pdf_page_count": None,
        "status": "pending",
        "error": None,
    }


def save_manifest(data: Dict[str, Any]) -> None:
    data["scans"] = sorted(data["scans"], key=lambda s: s["logical_position"])
    path = config.MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".manifest_", suffix=".json.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_manifest() -> Optional[Dict[str, Any]]:
    path = config.MANIFEST_PATH
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_scan_entry(data: Dict[str, Any], logical_position: int) -> Optional[Dict[str, Any]]:
    for s in data["scans"]:
        if s["logical_position"] == logical_position:
            return s
    return None
