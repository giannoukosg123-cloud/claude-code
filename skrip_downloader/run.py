"""CLI entry point.

Usage:
    python3 -m skrip_downloader.run --dates 1922-01-01 1922-01-03
    python3 -m skrip_downloader.run --year 1922
    python3 -m skrip_downloader.run --from 1924-10-01 --to 1924-12-31

--dates processes an explicit list of dates (must all share one year), one
pass, one summary -- for small ad-hoc/live-validation checks.

--year and --from/--to both process every calendar date in the requested
span, sequentially, then immediately run a second full pass over the same
dates to verify that already-complete/missing issues are skipped and
nothing on disk changes. A span is grouped by calendar year internally so
each date's log lines and registry entry always land in that date's own
year's files (data/raw/skrip/<year>/issues_registry.json,
logs/skrip/<year>.log) -- years are never mixed, even if a --from/--to
span happened to cross a year boundary.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
from typing import Any, Dict, List

from . import config, registry
from .logging_setup import setup_logger, log_event
from .pipeline import process_date
from .verification import (
    diff_snapshots,
    find_cross_contamination,
    snapshot_merged_pdfs,
    snapshot_raw_page_files,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SKRIP (ΣΚΡΙΠ) issue downloader")
    parser.add_argument(
        "--dates",
        nargs="+",
        help="Dates to process, format YYYY-MM-DD (e.g. 1922-01-01 1922-01-03). "
             "Single pass, must all share one year.",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Process every calendar date in this year, sequentially, with an "
             "automatic second verification pass (e.g. 1922).",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        help="Start date YYYY-MM-DD of a range (must be used together with --to).",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        help="End date YYYY-MM-DD of a range, inclusive (must be used together with --from).",
    )
    args = parser.parse_args()

    modes_selected = sum([
        bool(args.dates),
        args.year is not None,
        bool(args.date_from or args.date_to),
    ])
    if modes_selected != 1:
        parser.error("Specify exactly one of: --dates, --year, or --from/--to together.")
    if bool(args.date_from) != bool(args.date_to):
        parser.error("--from and --to must be given together.")

    return args


def year_dates(year: int) -> List[date]:
    return dates_in_range(date(year, 1, 1), date(year, 12, 31))


def dates_in_range(date_from: date, date_to: date) -> List[date]:
    if date_to < date_from:
        raise ValueError(f"--to ({date_to}) must not be before --from ({date_from}).")
    out = []
    d = date_from
    while d <= date_to:
        out.append(d)
        d += timedelta(days=1)
    return out


def group_by_year(dates: List[date]) -> Dict[int, List[date]]:
    groups: Dict[int, List[date]] = {}
    for d in dates:
        groups.setdefault(d.year, []).append(d)
    return groups


def safe_process_date(day: int, month: int, year: int, logger) -> Dict[str, Any]:
    """process_date must never take down an unattended crawl. Any exception
    that escapes it is recorded as this one date's status ("error", never
    "missing") and the crawl moves on to the next date."""
    issue_date = f"{year:04d}-{month:02d}-{day:02d}"
    issue_id = f"{config.NEWSPAPER_CODE}_{issue_date}"
    try:
        return process_date(day, month, year, logger)
    except Exception as exc:  # noqa: BLE001
        log_event(
            logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
            action="PROCESS_DATE_FATAL_ERROR", source_url=None, local_path=None,
            status="error", extra=f"error={exc}",
        )
        registry.update_registry_entry(
            year, issue_date, issue_id=issue_id, status="error",
            pages_expected=0, pages_downloaded=0, merged_pdf_path=None,
            error=f"unhandled exception in process_date: {exc}",
        )
        return {"status": "error", "issue_id": issue_id, "pages_expected": 0, "pages_downloaded": 0}


def run_dates(dates: List[date], logger) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for d in dates:
        print(f"=== Processing {d.isoformat()} ===")
        result = safe_process_date(d.day, d.month, d.year, logger)
        results[d.isoformat()] = result
        print(
            f"    status={result.get('status')} "
            f"pages_downloaded={result.get('pages_downloaded')}/{result.get('pages_expected')}"
        )
    return results


def summarize(results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    counts = Counter(r.get("status") for r in results.values())
    total_pages = sum(r.get("pages_downloaded") or 0 for r in results.values())
    merged = [r["merged_pdf"] for r in results.values() if r.get("merged_pdf")]
    total_bytes = sum(m["bytes"] for m in merged)

    return {
        "total_dates_checked": len(results),
        "complete": sorted(d for d, r in results.items() if r.get("status") == "complete"),
        "missing": sorted(d for d, r in results.items() if r.get("status") == "missing"),
        "partial": sorted(d for d, r in results.items() if r.get("status") == "partial"),
        "error": sorted(d for d, r in results.items() if r.get("status") == "error"),
        "counts": dict(counts),
        "total_pages_downloaded": total_pages,
        "total_merged_pdfs": len(merged),
        "total_bytes_downloaded": total_bytes,
    }


def print_summary(title: str, summary: Dict[str, Any], years: List[int]) -> None:
    print(f"\n===== {title} =====")
    print(f"Total dates checked: {summary['total_dates_checked']}")
    print(f"Complete: {len(summary['complete'])}")
    print(f"Missing:  {len(summary['missing'])}")
    print(f"Partial:  {len(summary['partial'])}")
    print(f"Error:    {len(summary['error'])}")
    print(f"Total pages downloaded: {summary['total_pages_downloaded']}")
    print(f"Total merged PDFs: {summary['total_merged_pdfs']}")
    print(f"Total bytes downloaded (merged PDFs): {summary['total_bytes_downloaded']}")
    for y in years:
        print(f"[{y}] issues_registry.json: {registry.registry_path(y)}")
        print(f"[{y}] log file: {config.LOG_ROOT / f'{y}.log'}")
        print(f"[{y}] merged issues folder: {config.ISSUES_ROOT / str(y)}")

    if summary["missing"]:
        print(f"\nMissing dates ({len(summary['missing'])}):")
        for d in summary["missing"]:
            print(f"  {d}")
    if summary["partial"]:
        print(f"\nPartial dates ({len(summary['partial'])}):")
        for d in summary["partial"]:
            print(f"  {d}")
    if summary["error"]:
        print(f"\nError dates ({len(summary['error'])}):")
        for d in summary["error"]:
            print(f"  {d}")


def print_directory_tree(year: int) -> None:
    print(f"\n===== Directory tree (summary) - {year} =====")
    raw_year_dir = config.RAW_ROOT / str(year)
    issues_year_dir = config.ISSUES_ROOT / str(year)

    if raw_year_dir.exists():
        issue_dirs = sorted(p for p in raw_year_dir.iterdir() if p.is_dir())
        print(f"{raw_year_dir}/  ({len(issue_dirs)} issue folders)")
        for p in issue_dirs[:5]:
            n_pages = len(list((p / "pages").glob("*.pdf"))) if (p / "pages").exists() else 0
            manifest_note = "manifest.json" if (p / "manifest.json").exists() else "no manifest.json (fetch error, never resolved)"
            print(f"  {p.name}/pages/  ({n_pages} pdfs), {manifest_note}")
        if len(issue_dirs) > 5:
            print(f"  ... ({len(issue_dirs) - 5} more issue folders)")
        print(f"  issues_registry.json")

    if issues_year_dir.exists():
        merged_files = sorted(issues_year_dir.glob("*.pdf"))
        print(f"{issues_year_dir}/  ({len(merged_files)} merged PDFs)")
        for p in merged_files[:5]:
            print(f"  {p.name}")
        if len(merged_files) > 5:
            print(f"  ... ({len(merged_files) - 5} more merged PDFs)")


def run_crawl(dates: List[date], label: str) -> None:
    """Shared two-pass (crawl + verify) driver behind both --year and
    --from/--to. Groups dates by calendar year so every date's log lines
    and registry entry go exclusively to that year's own files."""
    groups = group_by_year(dates)
    years = sorted(groups)
    loggers = {y: setup_logger(y) for y in years}

    print(f"########## PASS 1: {label} ({len(dates)} dates across year(s) {years}) ##########")
    results_pass1: Dict[str, Dict[str, Any]] = {}
    for y in years:
        results_pass1.update(run_dates(groups[y], loggers[y]))
    summary1 = summarize(results_pass1)
    print_summary(f"PASS 1 SUMMARY - {label}", summary1, years)

    violations: List[str] = []
    for y in years:
        violations.extend(find_cross_contamination(y))
    print(f"\nCross-contamination check: {'CLEAN' if not violations else 'VIOLATIONS FOUND'}")
    for v in violations:
        print(f"  VIOLATION: {v}")

    merged_before = {y: snapshot_merged_pdfs(y) for y in years}
    pages_before = {y: snapshot_raw_page_files(y) for y in years}

    print(f"\n########## PASS 2: verification re-run - {label} ##########")
    results_pass2: Dict[str, Dict[str, Any]] = {}
    for y in years:
        results_pass2.update(run_dates(groups[y], loggers[y]))
    summary2 = summarize(results_pass2)
    print_summary(f"PASS 2 SUMMARY - {label}", summary2, years)

    print("\n===== PASS 2 IDEMPOTENCY VERIFICATION =====")
    idempotent_all = True
    for y in years:
        merged_diff = diff_snapshots(merged_before[y], snapshot_merged_pdfs(y))
        pages_diff = diff_snapshots(pages_before[y], snapshot_raw_page_files(y))
        print(f"-- Year {y} --")
        print(f"  Merged PDFs added:   {merged_diff['added'] or 'none'}")
        print(f"  Merged PDFs removed: {merged_diff['removed'] or 'none'}")
        print(f"  Merged PDFs changed (sha256/bytes differ): {merged_diff['changed'] or 'none'}")
        print(f"  Raw page files added/removed/changed: "
              f"{len(pages_diff['added'])}/{len(pages_diff['removed'])}/{len(pages_diff['changed'])}")
        if any(merged_diff["added"] or merged_diff["removed"] or merged_diff["changed"]
               or pages_diff["added"] or pages_diff["removed"] or pages_diff["changed"]):
            idempotent_all = False

    print(f"\nIDEMPOTENT SECOND RUN: {'YES' if idempotent_all else 'NO -- INVESTIGATE'}")

    for y in years:
        print_directory_tree(y)
        print(f"\nAll merged PDFs for {y} are located together under: {config.ISSUES_ROOT / str(y)}")


def main() -> None:
    args = parse_args()

    if args.year is not None:
        run_crawl(year_dates(args.year), label=f"full-year crawl for {args.year}")
        return

    if args.date_from:
        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to)
        dates = dates_in_range(date_from, date_to)
        run_crawl(dates, label=f"date-range crawl {date_from.isoformat()} -> {date_to.isoformat()}")
        return

    # --dates: single explicit list, single pass, single year (original behavior).
    dates = [date.fromisoformat(d) for d in args.dates]
    years = {d.year for d in dates}
    if len(years) != 1:
        raise SystemExit("All --dates must be within the same year for a single run.")
    year = years.pop()

    logger = setup_logger(year)
    results = run_dates(sorted(dates), logger)
    summary = summarize(results)
    print_summary(f"SUMMARY - {year}", summary, [year])


if __name__ == "__main__":
    main()
