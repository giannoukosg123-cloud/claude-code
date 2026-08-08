"""Cross-issue isolation checks and merged-PDF snapshot/compare helpers,
used to prove a second full-year pass changes nothing on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from . import config
from .validation import sha256_of_file


def find_cross_contamination(year: int) -> List[str]:
    """Every file under an issue's pages/ dir must be named after that
    issue's own issue_id. Returns a list of human-readable violations
    (empty list == clean)."""
    violations: List[str] = []
    year_dir = config.RAW_ROOT / str(year)
    if not year_dir.exists():
        return violations

    for issue_dir in sorted(year_dir.iterdir()):
        if not issue_dir.is_dir():
            continue
        issue_id = issue_dir.name
        pages_dir = issue_dir / "pages"
        if not pages_dir.exists():
            continue
        for f in pages_dir.iterdir():
            if not f.name.startswith(f"{issue_id}_p"):
                violations.append(f"{f} does not belong to {issue_id}")
    return violations


def snapshot_merged_pdfs(year: int) -> Dict[str, Dict[str, object]]:
    """issue_id -> {path, bytes, sha256} for every merged PDF currently on disk."""
    snap: Dict[str, Dict[str, object]] = {}
    year_dir = config.ISSUES_ROOT / str(year)
    if not year_dir.exists():
        return snap
    for f in sorted(year_dir.glob("*.pdf")):
        snap[f.stem] = {
            "path": str(f),
            "bytes": f.stat().st_size,
            "sha256": sha256_of_file(f),
        }
    return snap


def snapshot_raw_page_files(year: int) -> Dict[str, int]:
    """relative_path -> size_bytes, for every page PDF currently on disk."""
    snap: Dict[str, int] = {}
    year_dir = config.RAW_ROOT / str(year)
    if not year_dir.exists():
        return snap
    for f in sorted(year_dir.rglob("*.pdf")):
        snap[str(f.relative_to(year_dir))] = f.stat().st_size
    return snap


def diff_snapshots(before: Dict, after: Dict) -> Dict[str, list]:
    before_keys, after_keys = set(before), set(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    changed = sorted(k for k in (before_keys & after_keys) if before[k] != after[k])
    return {"added": added, "removed": removed, "changed": changed}
