"""Small diagnostic download -- item=40221 seg=7340, logical positions 1..20 ONLY.

Purpose: the sanity test on this segment already found positions 1-3
continuing the same issue (1/10/1937) but position 4 visually showing a
page from September 1937 -- so logical_position here cannot yet be
trusted as a reliable page/issue sequence either. This downloads a larger
sample (20 scans) for manual visual inspection of each one's printed
issue date and page number. NOT a bulk download, NOT OCR, NOT merge.

Uses the SAME confirmed mechanism as run.py (header.asp mapping is the
only source of truth for current_id -- never guessed arithmetically), but
writes to a completely separate location so it can never collide with or
overwrite this record's (or any other record's) raw scans/manifest.json:

    Parliament_Downloader/diagnostic/item_40221_seg_7340_positions_1_20/
        diag_pos_0001_current_<ID>.pdf
        ...
        diag_pos_0020_current_<ID>.pdf
        diagnostic_item40221_seg7340_positions_1_20.json

Usage:
    python -m parliament_downloader.diagnostic_range_40221
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Dict, List

from . import config, discovery, http_client, pipeline
from .logging_setup import setup_logger, log_event
from .validation import validate_pdf_file

RECORD = config.RECORDS[(40221, 7340)]

START_POSITION = 1
END_POSITION = 20  # inclusive

DIAGNOSTIC_DIR = config.REPO_ROOT / "diagnostic" / f"item_{RECORD.item}_seg_{RECORD.seg}_positions_{START_POSITION}_{END_POSITION}"
DIAGNOSTIC_JSON_PATH = DIAGNOSTIC_DIR / f"diagnostic_item{RECORD.item}_seg{RECORD.seg}_positions_{START_POSITION}_{END_POSITION}.json"


def diag_filename(logical_position: int, current_id: int) -> str:
    return f"diag_pos_{logical_position:04d}_current_{current_id}.pdf"


def download_one(session, entry: discovery.DiscoveredEntry, referer: str, logger) -> Dict[str, Any]:
    local_path = DIAGNOSTIC_DIR / diag_filename(entry.logical_position, entry.current_id)
    result: Dict[str, Any] = {
        "logical_position": entry.logical_position,
        "current_id": entry.current_id,
        "source_url": discovery.main_url(entry.current_id),
        "local_filename": local_path.name,
        "bytes": None,
        "sha256": None,
        "validation_status": "pending",
    }

    response = http_client.get_with_retry(
        session, result["source_url"], logger=logger, log_label="main.asp-diagnostic", stream=True, referer=referer,
    )
    if response.status_code != 200:
        result["validation_status"] = f"error_http_{response.status_code}"
        log_event(
            logger, RECORD, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DIAGNOSTIC_DOWNLOAD_FAIL", source_url=result["source_url"], local_path=str(local_path),
            status="error", extra=f"http_status={response.status_code}",
        )
        return result

    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(DIAGNOSTIC_DIR), prefix=".dl_", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        os.replace(tmp_path, local_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    check = validate_pdf_file(local_path)
    result["bytes"] = check.size_bytes
    if check.ok:
        result["sha256"] = check.sha256
        result["validation_status"] = "ok"
        log_event(
            logger, RECORD, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DIAGNOSTIC_DOWNLOAD_OK", source_url=result["source_url"], local_path=str(local_path),
            status="ok", extra=f"bytes={check.size_bytes} sha256={check.sha256} pages={check.page_count}",
        )
    else:
        result["validation_status"] = f"error_{check.error}"
        log_event(
            logger, RECORD, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DIAGNOSTIC_VALIDATE_FAIL", source_url=result["source_url"], local_path=str(local_path),
            status="error", extra=f"error={check.error}",
        )

    return result


def main() -> None:
    logger = setup_logger(RECORD)
    session = http_client.new_session()

    try:
        entries, strategy, header_url = pipeline.run_discovery(session, RECORD, logger)
    except pipeline.DiscoveryError as exc:
        print("DISCOVERY FAILED -- stopping before any diagnostic download:")
        print(str(exc))
        sys.exit(1)

    entries_by_position = {e.logical_position: e for e in entries}
    positions = list(range(START_POSITION, END_POSITION + 1))
    missing = [p for p in positions if p not in entries_by_position]
    if missing:
        print(f"FATAL: logical position(s) {missing} not found in discovery. Stopping.")
        sys.exit(1)

    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, Any]] = []
    for pos in positions:
        entry = entries_by_position[pos]
        record = download_one(session, entry, referer=header_url, logger=logger)
        records.append(record)

    DIAGNOSTIC_JSON_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nposition -> current_id mapping:")
    for r in records:
        print(f"  {r['logical_position']} -> {r['current_id']}")
    print(f"\ndiagnostic folder: {DIAGNOSTIC_DIR}")
    print(f"diagnostic JSON:   {DIAGNOSTIC_JSON_PATH}")


if __name__ == "__main__":
    main()
