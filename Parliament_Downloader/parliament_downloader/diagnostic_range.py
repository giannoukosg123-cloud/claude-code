"""Small diagnostic download -- logical positions 155..170 ONLY.

Purpose: let a human visually inspect these 16 PDFs and note each one's
printed issue date and page number, to check whether logical_position is
actually a continuous page/issue sequence despite the large current_id
jumps already observed. NOT a bulk download, NOT OCR, NOT merge, NOT
Excel -- purely fetching 16 files for manual visual inspection.

Uses the SAME confirmed mechanism as run.py (header.asp mapping is the
only source of truth for current_id -- never guessed arithmetically), but
writes to a completely separate location so it can never collide with or
overwrite the main downloader's raw scans/manifest.json:

    Parliament_Downloader/diagnostic/positions_155_170/
        diag_pos_0155_current_<ID>.pdf
        ...
        diag_pos_0170_current_<ID>.pdf
        diagnostic_positions_155_170.json

Usage:
    python -m parliament_downloader.diagnostic_range
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from . import config, discovery, http_client, pipeline
from .logging_setup import setup_logger, log_event
from .validation import sha256_of_file, validate_pdf_file

START_POSITION = 155
END_POSITION = 170  # inclusive

DIAGNOSTIC_DIR = config.REPO_ROOT / "diagnostic" / f"positions_{START_POSITION}_{END_POSITION}"
DIAGNOSTIC_JSON_PATH = DIAGNOSTIC_DIR / f"diagnostic_positions_{START_POSITION}_{END_POSITION}.json"


def diag_filename(logical_position: int, current_id: int) -> str:
    return f"diag_pos_{logical_position:04d}_current_{current_id}.pdf"


def download_one(session, entry: discovery.DiscoveredEntry, referer: str, logger) -> Dict[str, Any]:
    local_path = DIAGNOSTIC_DIR / diag_filename(entry.logical_position, entry.current_id)
    record: Dict[str, Any] = {
        "logical_position": entry.logical_position,
        "current_id": entry.current_id,
        "source_url": discovery.main_url(entry.current_id),
        "local_filename": local_path.name,
        "bytes": None,
        "sha256": None,
        "validation_status": "pending",
    }

    response = http_client.get_with_retry(
        session, record["source_url"], logger=logger, log_label="main.asp-diagnostic", stream=True, referer=referer,
    )
    if response.status_code != 200:
        record["validation_status"] = f"error_http_{response.status_code}"
        log_event(
            logger, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DIAGNOSTIC_DOWNLOAD_FAIL", source_url=record["source_url"], local_path=str(local_path),
            status="error", extra=f"http_status={response.status_code}",
        )
        return record

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
    record["bytes"] = check.size_bytes
    if check.ok:
        record["sha256"] = check.sha256
        record["validation_status"] = "ok"
        log_event(
            logger, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DIAGNOSTIC_DOWNLOAD_OK", source_url=record["source_url"], local_path=str(local_path),
            status="ok", extra=f"bytes={check.size_bytes} sha256={check.sha256} pages={check.page_count}",
        )
    else:
        record["validation_status"] = f"error_{check.error}"
        log_event(
            logger, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DIAGNOSTIC_VALIDATE_FAIL", source_url=record["source_url"], local_path=str(local_path),
            status="error", extra=f"error={check.error}",
        )

    return record


def main() -> None:
    logger = setup_logger()
    session = http_client.new_session()

    print("########## DISCOVERY (using the existing verified header.asp mapping) ##########")
    try:
        entries, strategy, header_url = pipeline.run_discovery(session, logger)
    except pipeline.DiscoveryError as exc:
        print("\nDISCOVERY FAILED -- stopping before any diagnostic download:")
        print(str(exc))
        sys.exit(1)

    print(f"Discovery OK: {len(entries)} entries via strategy={strategy!r}.")

    entries_by_position = {e.logical_position: e for e in entries}
    positions = list(range(START_POSITION, END_POSITION + 1))
    missing = [p for p in positions if p not in entries_by_position]
    if missing:
        print(f"\nFATAL: logical position(s) {missing} not found in discovery. Stopping.")
        sys.exit(1)

    print(f"\n########## DOWNLOADING logical positions {START_POSITION}..{END_POSITION} "
          f"({len(positions)} scans) ##########")
    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, Any]] = []
    for pos in positions:
        entry = entries_by_position[pos]
        print(f"=== pos={pos} current_id={entry.current_id} ===")
        record = download_one(session, entry, referer=header_url, logger=logger)
        records.append(record)
        print(f"    validation_status={record['validation_status']} bytes={record['bytes']}")

    DIAGNOSTIC_JSON_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    ok_count = sum(1 for r in records if r["validation_status"] == "ok")

    print("\n===== DIAGNOSTIC RESULT =====")
    print(f"{ok_count}/{len(records)} downloaded and validated ok")
    print("\nlogical_position -> current_id mapping:")
    for r in records:
        print(f"  {r['logical_position']} -> {r['current_id']}  ({r['validation_status']})")
    print(f"\ndiagnostic folder: {DIAGNOSTIC_DIR}")
    print(f"diagnostic JSON:   {DIAGNOSTIC_JSON_PATH}")


if __name__ == "__main__":
    main()
