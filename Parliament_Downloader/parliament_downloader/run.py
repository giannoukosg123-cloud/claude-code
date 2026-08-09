"""CLI entry point.

Usage (original segment, item=40489 seg=7597 -- default, unchanged):
    python -m parliament_downloader.run --positions 1 2 163 164 546
    python -m parliament_downloader.run --bulk

Usage (any other registered segment, e.g. item=40221 seg=7340):
    python -m parliament_downloader.run --item 40221 --seg 7340 --positions 1 2 3 573 574
    python -m parliament_downloader.run --item 40221 --seg 7340 --bulk

--item/--seg select which config.Record to operate on (see config.py's
RECORDS registry -- a new segment must be registered there first). Omit
both to use the original default record; --item and --seg must be given
together otherwise.

--positions downloads only the given logical positions (after full
discovery + the record's expected_scans-count gate) -- for the pre-bulk
sanity test.
--bulk downloads all of the selected record's expected scans.

Both modes run discovery first and refuse to download anything if the
header.asp parse doesn't yield exactly expected_scans entries -- see
pipeline.run_discovery / DiscoveryError.

Every record is fully isolated: its own data/raw/.../scans/ folder, its
own manifest.json, its own log file, its own logger instance. Nothing
here ever reads or writes another record's files.

No merge. No OCR. No Excel. No NRA integration. No grouping by issue/date.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List

from . import config, http_client, manifest as manifest_mod, pipeline
from .logging_setup import setup_logger, log_event


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parliament_Downloader -- ΑΚΡΟΠΟΛΙΣ (digitallib.parliament.gr)")
    parser.add_argument("--item", type=int, help="item id (must be given together with --seg)")
    parser.add_argument("--seg", type=int, help="segment id (must be given together with --item)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--positions", nargs="+", type=int,
        help="Specific logical positions to download (test mode), e.g. 1 2 163 164 546",
    )
    group.add_argument(
        "--bulk", action="store_true",
        help="Download all of the selected record's expected scans after discovery succeeds",
    )
    return parser.parse_args()


def print_final_report(
    record: config.Record, manifest_data: Dict[str, Any], outcomes: Dict[int, str], requested_positions: List[int],
) -> None:
    scans = manifest_data["scans"]
    all_positions = sorted(s["logical_position"] for s in scans)
    expected_positions = set(range(1, record.expected_scans + 1))
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
    print(f"item_id: {record.item}  segment_id: {record.seg}")
    print(f"expected_scans: {manifest_data['expected_scans']}")
    print(f"discovered_scans: {manifest_data['discovered_scans']}")
    print(f"requested this run: {len(requested_positions)}")
    print(f"downloaded ok: {len(downloaded_ok)}")
    print(f"skipped ok (already valid): {len(skipped_ok)}")
    print(f"failed/error: {len(errors)}")
    print(f"total bytes (this run's requested set, status=ok): {total_bytes}")
    print(f"missing logical positions (across the whole manifest, 1..{record.expected_scans}): "
          f"{missing_positions if missing_positions else 'none'}")
    print(f"duplicate current_ids (across the whole manifest): "
          f"{duplicate_current_ids if duplicate_current_ids else 'none'}")
    if errors:
        print(f"\nError positions this run: {sorted(errors)}")
        for p in sorted(errors):
            scan = manifest_mod.find_scan_entry(manifest_data, p)
            print(f"  pos={p} current_id={scan['current_id']} error={scan.get('error')}")
    print(f"\nmanifest: {record.manifest_path}")
    print(f"log: {record.log_path}")
    print(f"scans folder: {record.scans_dir}")
    print("\nNO MERGE PERFORMED")
    print("NO OCR PERFORMED")
    print("NO EXCEL CREATED")
    print("NO NRA INTEGRATION")


def main() -> None:
    args = parse_args()

    try:
        record = config.get_record(args.item, args.seg)
    except ValueError as exc:
        print(f"FATAL: {exc}")
        sys.exit(1)

    logger = setup_logger(record)
    session = http_client.new_session()

    print(f"########## DISCOVERY (item={record.item} seg={record.seg}) ##########")
    try:
        entries, strategy, header_url = pipeline.run_discovery(session, record, logger)
    except pipeline.DiscoveryError as exc:
        log_event(
            logger, record, logical_position=None, current_id=None, action="DISCOVERY_FATAL",
            source_url=None, local_path=None, status="error", extra=str(exc),
        )
        print("\n===== DISCOVERY DIAGNOSTIC REPORT =====")
        print(str(exc))
        print("\nStopping before any download, as required. Fix header.asp parsing "
              "(see parliament_downloader/discovery.py) and re-run.")
        sys.exit(1)

    print(f"Discovery OK: {len(entries)} entries found via strategy={strategy!r} "
          f"(expected {record.expected_scans}).")
    print(f"First entry: pos={entries[0].logical_position} current_id={entries[0].current_id}")
    print(f"Last entry:  pos={entries[-1].logical_position} current_id={entries[-1].current_id}")

    manifest_data = manifest_mod.load_manifest(record)
    if manifest_data is None:
        manifest_data = manifest_mod.new_manifest(record, discovered_scans=len(entries))
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
                  f"(valid range is 1..{record.expected_scans}). Aborting.")
            sys.exit(1)

    print(f"\n########## {'BULK DOWNLOAD' if args.bulk else 'TEST DOWNLOAD'} "
          f"({len(requested_positions)} scan(s)) ##########")

    outcomes: Dict[int, str] = {}
    for pos in requested_positions:
        entry = entries_by_position[pos]
        print(f"=== scan pos={pos} current_id={entry.current_id} ===")
        outcome = pipeline.process_scan(session, record, entry, manifest_data, referer=header_url, logger=logger)
        outcomes[pos] = outcome
        print(f"    outcome={outcome}")

    manifest_mod.save_manifest(record, manifest_data)

    print_final_report(record, manifest_data, outcomes, requested_positions)

    if not args.bulk:
        failed = [p for p, o in outcomes.items() if o == "error"]
        item_seg_flag = "" if record is config.DEFAULT_RECORD else f"--item {record.item} --seg {record.seg} "
        if failed:
            print(f"\nTEST FAILED for position(s) {failed} -- do NOT proceed to --bulk until resolved.")
            sys.exit(1)
        print(f"\nAll {len(requested_positions)} test positions passed. "
              f"Safe to proceed with: python -m parliament_downloader.run {item_seg_flag}--bulk")


if __name__ == "__main__":
    main()
