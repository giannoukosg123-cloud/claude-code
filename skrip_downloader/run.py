"""CLI entry point.

Usage:
    python3 -m skrip_downloader.run --dates 1922-01-01 1922-01-03
    python3 -m skrip_downloader.run --year 1922
    python3 -m skrip_downloader.run --from 1924-10-01 --to 1924-12-31
    python3 -m skrip_downloader.run --verify --from 1923-01-01 --to 1923-10-31
    python3 -m skrip_downloader.run --find-next 1923-01-22

--dates processes an explicit list of dates (must all share one year), one
pass, one summary -- for small ad-hoc/live-validation checks.

--year and --from/--to both process every calendar date in the requested
span, sequentially, then immediately run a local, offline, read-only
verification pass over the same dates (manifest/registry consistency, raw
page and merged PDF sha256 checks, cross-issue contamination, duplicate
merged PDFs) -- never a second real crawl. A span is grouped by calendar
year internally so each date's log lines and registry entry always land in
that date's own year's files (data/raw/skrip/<year>/issues_registry.json,
logs/skrip/<year>.log) -- years are never mixed, even if a --from/--to span
happened to cross a year boundary.

--verify is a modifier: combined with --dates/--year/--from+--to, it skips
the crawl entirely and only runs the offline verification pass described
above. No network requests, no writes to manifest.json/issues_registry.json,
not even a log file is created.

--find-next YYYY-MM-DD is a discovery mode: starting from that date
(inclusive), it live-rechecks dates sequentially through Dec 31 of that
year -- ignoring any stale "missing" a previous crawl may have recorded --
until it finds the first date with a live-confirmed available issue,
downloads+merges only that one issue, then stops. See
skrip_downloader/find_next.py for the full contract.
"""

from __future__ import annotations

import argparse
import calendar
from collections import Counter
from datetime import date, timedelta
from typing import Any, Dict, List

from . import config, find_next, local_verify, registry
from .logging_setup import setup_logger, log_event
from .pipeline import process_date


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
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run ONLY local, offline verification over the selected dates -- no crawl, "
             "no network calls, no writes to manifest.json or issues_registry.json. "
             "Must be combined with --dates, --year, or --from/--to.",
    )
    parser.add_argument(
        "--find-next",
        dest="find_next",
        metavar="YYYY-MM-DD",
        help="Search forward from this date (inclusive) through Dec 31 of that year for "
             "the first live-confirmed available issue; downloads+merges only that one "
             "issue, then stops. Live-rechecks every date whose last known status isn't "
             "'complete' (ignores stale 'missing' from a previous crawl). A standalone mode.",
    )
    args = parser.parse_args()

    modes_selected = sum([
        bool(args.dates),
        args.year is not None,
        bool(args.date_from or args.date_to),
        bool(args.find_next),
    ])
    if modes_selected != 1:
        parser.error("Specify exactly one of: --dates, --year, --from/--to, or --find-next.")
    if bool(args.date_from) != bool(args.date_to):
        parser.error("--from and --to must be given together.")
    if args.find_next and args.verify:
        parser.error("--verify cannot be combined with --find-next (find-next always live-checks).")

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


def monthly_breakdown(results: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """{'YYYY-MM': {'complete': n, 'missing': n, 'partial': n, 'error': n, 'total': n}}, sorted chronologically."""
    counters: Dict[str, Counter] = {}
    for date_str, r in results.items():
        year_month = date_str[:7]
        counters.setdefault(year_month, Counter())[r.get("status")] += 1

    breakdown = {}
    for year_month in sorted(counters):
        c = counters[year_month]
        breakdown[year_month] = {
            "complete": c.get("complete", 0),
            "missing": c.get("missing", 0),
            "partial": c.get("partial", 0),
            "error": c.get("error", 0),
            "total": sum(c.values()),
        }
    return breakdown


def print_monthly_breakdown(title: str, results: Dict[str, Dict[str, Any]]) -> None:
    print(f"\n===== {title} =====")
    for year_month, counts in monthly_breakdown(results).items():
        y, m = year_month.split("-")
        month_name = calendar.month_name[int(m)]
        print(
            f"{month_name} {y}: total={counts['total']:3d}  "
            f"complete={counts['complete']:3d}  missing={counts['missing']:3d}  "
            f"partial={counts['partial']:3d}  error={counts['error']:3d}"
        )


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


def run_crawl(dates: List[date], label: str, verify_only: bool = False) -> None:
    """Driver behind --year and --from/--to: a real crawl pass (unless
    verify_only) followed unconditionally by a local, offline, read-only
    verification pass (skrip_downloader.local_verify) -- never a second real
    crawl. Groups dates by calendar year so every date's log lines and
    registry entry go exclusively to that year's own files."""
    groups = group_by_year(dates)
    years = sorted(groups)

    if not verify_only:
        loggers = {y: setup_logger(y) for y in years}
        print(f"########## CRAWL: {label} ({len(dates)} dates across year(s) {years}) ##########")
        results: Dict[str, Dict[str, Any]] = {}
        for y in years:
            results.update(run_dates(groups[y], loggers[y]))
        summary = summarize(results)
        print_summary(f"CRAWL SUMMARY - {label}", summary, years)
        print_monthly_breakdown(f"MONTHLY BREAKDOWN - {label}", results)
    else:
        print(f"########## VERIFY ONLY (offline, no crawl, no network, no writes): "
              f"{label} ({len(dates)} dates across year(s) {years}) ##########")

    report = local_verify.run_local_verification(groups)
    local_verify.print_verification_report(report, years)

    for y in years:
        print_directory_tree(y)
        print(f"\nAll merged PDFs for {y} are located together under: {config.ISSUES_ROOT / str(y)}")


def main() -> None:
    args = parse_args()

    if args.find_next:
        start = date.fromisoformat(args.find_next)
        logger = setup_logger(start.year)
        report = find_next.find_next_available_issue(start, logger)
        find_next.print_find_next_report(report)
        return

    if args.year is not None:
        mode = "verify" if args.verify else "crawl"
        run_crawl(year_dates(args.year), label=f"full-year {mode} for {args.year}", verify_only=args.verify)
        return

    if args.date_from:
        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to)
        dates = dates_in_range(date_from, date_to)
        mode = "verify" if args.verify else "crawl"
        run_crawl(
            dates,
            label=f"date-range {mode} {date_from.isoformat()} -> {date_to.isoformat()}",
            verify_only=args.verify,
        )
        return

    # --dates: explicit list, must all share one year.
    dates = sorted(date.fromisoformat(d) for d in args.dates)
    years = {d.year for d in dates}
    if len(years) != 1:
        raise SystemExit("All --dates must be within the same year for a single run.")
    year = years.pop()

    if args.verify:
        run_crawl(dates, label=f"explicit-dates verify ({year})", verify_only=True)
        return

    # Original, untouched single-pass behavior -- no auto-verify, ever.
    logger = setup_logger(year)
    results = run_dates(dates, logger)
    summary = summarize(results)
    print_summary(f"SUMMARY - {year}", summary, [year])


if __name__ == "__main__":
    main()
