"""DISCOVERY -> DOWNLOAD -> VALIDATION -> MANIFEST -> RESUME.

Every function takes an explicit config.Record (item/seg/title/
segment_label/expected_scans + its own paths) rather than reading fixed
module-level globals, so two different segments can never leak into each
other's manifest, scans folder, or log file -- there is no shared mutable
state between them.

No merge, no OCR, no Excel, no NRA integration, no grouping by issue/date --
deliberately out of scope for this tool.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from . import config, discovery, download, manifest as manifest_mod
from .logging_setup import log_event
from .validation import validate_pdf_file


class DiscoveryError(RuntimeError):
    pass


def run_discovery(
    session, record: config.Record, logger: logging.Logger,
) -> Tuple[List[discovery.DiscoveredEntry], str, str]:
    """Runs library.asp -> display_doc.asp -> header.asp for this record and
    parses the full logical_position -> current_id mapping. Raises
    DiscoveryError with a diagnostic message if anything about this
    invariant fails -- this must never be silently patched over."""
    lib_resp = discovery.fetch_library(session, record, logger)
    log_event(
        logger, record, logical_position=None, current_id=None, action="FETCH_LIBRARY",
        source_url=discovery.library_url(record), local_path=None, status=str(lib_resp.status_code),
    )
    if lib_resp.status_code != 200:
        raise DiscoveryError(f"library.asp returned HTTP {lib_resp.status_code}")
    if not discovery.confirm_segment_present(lib_resp.text, record):
        raise DiscoveryError(
            f"library.asp did not mention seg={record.seg} anywhere -- wrong item/segment, or page layout changed."
        )

    doc_resp = discovery.fetch_display_doc(session, record, logger)
    log_event(
        logger, record, logical_position=None, current_id=None, action="FETCH_DISPLAY_DOC",
        source_url=discovery.display_doc_url(record), local_path=None, status=str(doc_resp.status_code),
    )
    if doc_resp.status_code != 200:
        raise DiscoveryError(f"display_doc.asp returned HTTP {doc_resp.status_code}")

    header_url = discovery.find_header_url(doc_resp.text, doc_resp.url)
    if not header_url:
        raise DiscoveryError(
            "Could not find a frame/iframe pointing to header.asp inside display_doc.asp's frameset."
        )

    header_resp = discovery.fetch_header(session, header_url, record, logger)
    log_event(
        logger, record, logical_position=None, current_id=None, action="FETCH_HEADER",
        source_url=header_url, local_path=None, status=str(header_resp.status_code),
    )
    if header_resp.status_code != 200:
        raise DiscoveryError(f"header.asp returned HTTP {header_resp.status_code}")

    entries, strategy = discovery.parse_header(header_resp.text, logger)
    log_event(
        logger, record, logical_position=None, current_id=None, action="DISCOVER_HEADER_ENTRIES",
        source_url=header_url, local_path=None, status="ok",
        extra=f"strategy={strategy} count={len(entries)}",
    )

    if len(entries) != record.expected_scans:
        positions = sorted(e.logical_position for e in entries)
        expected_positions = set(range(1, record.expected_scans + 1))
        missing = sorted(expected_positions - set(positions))
        extra_positions = sorted(set(positions) - expected_positions)
        current_ids = [e.current_id for e in entries]
        duplicates = sorted({c for c in current_ids if current_ids.count(c) > 1})
        raise DiscoveryError(
            f"item={record.item} seg={record.seg}: expected exactly {record.expected_scans} entries, "
            f"found {len(entries)} (strategy={strategy}). Missing logical positions: {missing[:20]}"
            f"{' ...' if len(missing) > 20 else ''}. Unexpected positions: {extra_positions[:20]}. "
            f"Duplicate current_ids: {duplicates[:20]}. Stopping before any download -- "
            f"header.asp markup may not match the assumed structure."
        )

    return entries, strategy, header_url


def process_scan(
    session,
    record: config.Record,
    entry: discovery.DiscoveredEntry,
    manifest_data: Dict[str, Any],
    referer: str,
    logger: logging.Logger,
) -> str:
    """Download + validate one scan for this record, update manifest_data in
    place, resuming if a valid file already exists. Returns one of
    "skipped_ok", "downloaded_ok", "error" for the caller's tallying."""
    scan_entry = manifest_mod.find_scan_entry(manifest_data, entry.logical_position)
    if scan_entry is None:
        scan_entry = manifest_mod.new_scan_entry(record, entry.logical_position, entry.current_id, entry.source_url)
        manifest_data["scans"].append(scan_entry)

    local_path = record.scans_dir / scan_entry["local_filename"]

    if scan_entry.get("status") == "ok" and local_path.exists():
        check = validate_pdf_file(local_path)
        if check.ok and check.sha256 == scan_entry.get("sha256"):
            log_event(
                logger, record, logical_position=entry.logical_position, current_id=entry.current_id,
                action="SKIP_OK", source_url=scan_entry.get("source_url"), local_path=str(local_path),
                status="skipped",
            )
            return "skipped_ok"
        log_event(
            logger, record, logical_position=entry.logical_position, current_id=entry.current_id,
            action="RESUME_REVALIDATE_FAILED", source_url=None, local_path=str(local_path),
            status="failed", extra=f"error={check.error}",
        )

    outcome = "error"
    try:
        download.download_scan_bytes(session, entry.current_id, local_path, logger, referer=referer)
        check = validate_pdf_file(local_path)
        if not check.ok:
            log_event(
                logger, record, logical_position=entry.logical_position, current_id=entry.current_id,
                action="VALIDATE_FAIL", source_url=discovery.main_url(entry.current_id),
                local_path=str(local_path), status="failed", extra=f"error={check.error}",
            )
            scan_entry.update({
                "bytes": check.size_bytes, "sha256": None, "pdf_page_count": None,
                "status": "error", "error": check.error,
            })
            outcome = "error"
        else:
            log_event(
                logger, record, logical_position=entry.logical_position, current_id=entry.current_id,
                action="DOWNLOAD_OK", source_url=discovery.main_url(entry.current_id),
                local_path=str(local_path), status="ok",
                extra=f"bytes={check.size_bytes} sha256={check.sha256} pages={check.page_count}",
            )
            scan_entry.update({
                "bytes": check.size_bytes, "sha256": check.sha256, "pdf_page_count": check.page_count,
                "status": "ok", "error": None,
            })
            outcome = "downloaded_ok"
    except Exception as exc:  # noqa: BLE001 - one scan's failure must not stop the rest
        log_event(
            logger, record, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DOWNLOAD_FAIL", source_url=discovery.main_url(entry.current_id),
            local_path=str(local_path), status="error", extra=f"error={exc}",
        )
        scan_entry.update({"status": "error", "error": str(exc)})
        outcome = "error"

    manifest_mod.save_manifest(record, manifest_data)
    return outcome
