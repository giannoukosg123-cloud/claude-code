"""CLI entry point.

Usage:
    python -m parliament_downloader.run --positions 1 2 163 164 546
    python -m parliament_downloader.run --bulk

--positions downloads only the given logical positions (after full
discovery + the 546-count gate) -- for the pre-bulk sanity test.
--bulk downloads all expected_scans (546) positions.

Both modes run discovery first and refuse to download anything if the
header.asp parse doesn't yield exactly EXPECTED_SCANS entries -- see
pipeline.run_discovery / DiscoveryError.

No merge. No OCR. No Excel. No NRA integration. No grouping by issue/date.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List

from . import config, discovery, http_client, manifest as manifest_mod, pipeline
from .logging_setup import setup_logger, log_event


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parliament_Downloader -- ΑΚΡΟΠΟΛΙΣ item=40489 seg=7597")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--positions", nargs="+", type=int,
        help="Specific logical positions to download (test mode), e.g. 1 2 163 164 546",
    )
    group.add_argument(
        "--bulk", action="store_true",
        help="Download all expected scans (546) after discovery succeeds",
    )
    return parser.parse_args()


def print_final_report(manifest_data: Dict[str, Any], outcomes: Dict[int, str], requested_positions: List[int]) -> None:
    scans = manifest_data["scans"]
    all_positions = sorted(s["logical_position"] for s in scans)
    expected_positions = set(range(1, config.EXPECTED_SCANS + 1))
    missing_positions = sorted(expected_positions - set(all_positions))

    current_ids = [s["current_id"] for s in scans]
    duplicate_current_ids = sorted({c for c in current_ids if current_ids.count(c) > 1})

    downloaded_ok = [p for p, o in outcomes.items() if o == "downloaded_ok"]
    skipped_ok = [p for p, o in outcomes.items() if o == "skipped_ok"]
    errors = [p for p, o in outcomes.items() if o == "error"]

    total_bytes = sum(
        (s["bytes"] or 0) for s in scans
        if s["logical_position"] in requested_positions and s["status"] == "ok"
    )

    print("\n===== FINAL REPORT =====")
    print(f"expected_scans: {manifest_data['expected_scans']}")
    print(f"discovered_scans: {manifest_data['discovered_scans']}")
    print(f"requested this run: {len(requested_positions)}")
    print(f"downloaded ok: {len(downloaded_ok)}")
    print(f"skipped ok (already valid): {len(skipped_ok)}")
    print(f"failed/error: {len(errors)}")
    print(f"total bytes (this run's requested set, status=ok): {total_bytes}")
    print(f"missing logical positions (across the whole manifest, 1..{config.EXPECTED_SCANS}): "
          f"{missing_positions if missing_positions else 'none'}")
    print(f"duplicate current_ids (across the whole manifest): "
          f"{duplicate_current_ids if duplicate_current_ids else 'none'}")
    if errors:
        print(f"\nError positions this run: {sorted(errors)}")
        for p in sorted(errors):
            scan = manifest_mod.find_scan_entry(manifest_data, p)
            print(f"  pos={p} current_id={scan['current_id']} error={scan.get('error')}")
    print(f"\nmanifest: {config.MANIFEST_PATH}")
    print(f"log: {config.LOG_PATH}")
    print(f"scans folder: {config.SCANS_DIR}")
    print("\nNO MERGE PERFORMED")
    print("NO OCR PERFORMED")
    print("NO EXCEL CREATED")
    print("NO NRA INTEGRATION")


def main() -> None:
    args = parse_args()
    logger = setup_logger()

    session = http_client.new_session()

    print("########## DISCOVERY ##########")
    try:
        entries, strategy, header_url = pipeline.run_discovery(session, logger)
    except pipeline.DiscoveryError as exc:
        log_event(
            logger, logical_position=None, current_id=None, action="DISCOVERY_FATAL",
            source_url=None, local_path=None, status="error", extra=str(exc),
        )
        print("\n===== DISCOVERY DIAGNOSTIC REPORT =====")
        print(str(exc))
        print("\nStopping before any download, as required. Fix header.asp parsing "
              "(see parliament_downloader/discovery.py) and re-run.")
        sys.exit(1)

    print(f"Discovery OK: {len(entries)} entries found via strategy={strategy!r} "
          f"(expected {config.EXPECTED_SCANS}).")
    print(f"First entry: pos={entries[0].logical_position} current_id={entries[0].current_id}")
    print(f"Last entry:  pos={entries[-1].logical_position} current_id={entries[-1].current_id}")

    manifest_data = manifest_mod.load_manifest()
    if manifest_data is None:
        manifest_data = manifest_mod.new_manifest(discovered_scans=len(entries))
    else:
        manifest_data["discovered_scans"] = len(entries)

    entries_by_position = {e.logical_position: e for e in entries}

    if args.bulk:
        requested_positions = sorted(entries_by_position.keys())
    else:
        requested_positions = sorted(args.positions)
        unknown = [p for p in requested_positions if p not in entries_by_position]
        if unknown:
            print(f"\nFATAL: requested logical position(s) {unknown} were not found in discovery "
                  f"(valid range is 1..{config.EXPECTED_SCANS}). Aborting.")
            sys.exit(1)

    print(f"\n########## {'BULK DOWNLOAD' if args.bulk else 'TEST DOWNLOAD'} "
          f"({len(requested_positions)} scan(s)) ##########")

    outcomes: Dict[int, str] = {}
    for pos in requested_positions:
        entry = entries_by_position[pos]
        print(f"=== scan pos={pos} current_id={entry.current_id} ===")
        outcome = pipeline.process_scan(session, entry, manifest_data, referer=header_url, logger=logger)
        outcomes[pos] = outcome
        print(f"    outcome={outcome}")

    manifest_mod.save_manifest(manifest_data)

    print_final_report(manifest_data, outcomes, requested_positions)

    if not args.bulk:
        failed = [p for p, o in outcomes.items() if o == "error"]
        if failed:
            print(f"\nTEST FAILED for position(s) {failed} -- do NOT proceed to --bulk until resolved.")
            sys.exit(1)
        print(f"\nAll {len(requested_positions)} test positions passed. "
              f"Safe to proceed with: python -m parliament_downloader.run --bulk")


if __name__ == "__main__":
    main()
