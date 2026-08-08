"""Find-next-available-issue discovery mode.

Given a start date, scans forward day-by-day through the end of that
calendar year, live-rechecking every date whose last known status isn't
"complete" -- deliberately ignoring any stale "missing"/"partial"/"error"
recorded by a previous crawl, because this mode's whole purpose is finding
out what the NLG archive has *now*, not what a prior run last saw.

Every check and every download reuses the exact same confirmed
pipeline.process_date mechanism (pdfwin.asp -> pages.asp -> parse GetPdf ids
-> [if found] download -> validate -> merge -> validate merged -> registry
update) -- nothing here reimplements discovery, download, or merge. Stops at
the first issue that downloads and merges successfully, the first issue
that is found but fails, or the end of the year if nothing is found.

Sequential, one date at a time, no concurrency -- rate limiting is already
provided by http_client.get_with_retry's polite_delay() before every
request, exactly as it is for --year/--dates/--from+--to.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from . import config, manifest as manifest_mod, registry
from .logging_setup import log_event
from .pipeline import process_date


def _issue_id(d: date) -> str:
    return f"{config.NEWSPAPER_CODE}_{d.isoformat()}"


def _safe_process_date(d: date, logger: logging.Logger) -> Dict[str, Any]:
    """Same crash-safety contract as run.safe_process_date: process_date
    must never take down the search. Any exception is recorded as this
    date's status ("error", never "missing") and the search continues."""
    issue_date = d.isoformat()
    issue_id = _issue_id(d)
    try:
        return process_date(d.day, d.month, d.year, logger)
    except Exception as exc:  # noqa: BLE001
        log_event(
            logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
            action="FIND_NEXT_FATAL_ERROR", source_url=None, local_path=None,
            status="error", extra=f"error={exc}",
        )
        registry.update_registry_entry(
            d.year, issue_date, issue_id=issue_id, status="error",
            pages_expected=0, pages_downloaded=0, merged_pdf_path=None,
            error=f"unhandled exception in process_date during find-next: {exc}",
        )
        return {"status": "error", "issue_id": issue_id, "pages_expected": 0, "pages_downloaded": 0}


@dataclass
class FindNextReport:
    search_started: str
    search_end: str
    dates_checked: int = 0
    previously_missing_rechecked: int = 0
    error_dates: List[str] = field(default_factory=list)
    outcome: str = "NOT_FOUND"  # SUCCESS | NOT_FOUND | FOUND_BUT_FAILED
    found_date: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


def find_next_available_issue(start_date: date, logger: logging.Logger) -> FindNextReport:
    """Search [start_date, Dec 31 of start_date.year] inclusive. Stops and
    returns as soon as an issue completes, an issue is found but fails
    (status stays "partial"), or the year runs out."""
    end_date = date(start_date.year, 12, 31)
    report = FindNextReport(search_started=start_date.isoformat(), search_end=end_date.isoformat())

    d = start_date
    while d <= end_date:
        issue_date = d.isoformat()
        issue_id = _issue_id(d)
        report.dates_checked += 1

        existing = manifest_mod.load_manifest(d.year, issue_id)
        prior_status = existing.get("status") if existing else None

        if prior_status == "complete":
            print(f"=== {issue_date}: already complete -- skipped (no network) ===")
            log_event(
                logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
                action="FIND_NEXT_SKIP_COMPLETE", source_url=None, local_path=None, status="skipped",
            )
            d += timedelta(days=1)
            continue

        if prior_status == "missing":
            # A "missing" manifest carries zero downloaded content
            # (pages_expected=0) -- it is a pure marker, not data. Discarding
            # it here so process_date performs a completely fresh discovery
            # instead of taking its SKIP_MISSING shortcut is a correction,
            # not a loss: process_date rewrites this exact file with the
            # live result a few lines below, either way.
            manifest_mod.manifest_path(d.year, issue_id).unlink(missing_ok=True)
            report.previously_missing_rechecked += 1

        print(f"=== {issue_date}: live check (pdfwin.asp -> pages.asp) ===")
        result = _safe_process_date(d, logger)
        status = result.get("status")
        print(f"    status={status}")

        if status == "complete":
            print("\nNEXT AVAILABLE ISSUE FOUND:")
            print(issue_date)
            report.outcome = "SUCCESS"
            report.found_date = issue_date
            report.result = result
            return report

        if status == "partial":
            report.outcome = "FOUND_BUT_FAILED"
            report.found_date = issue_date
            report.result = result
            return report

        if status == "error":
            report.error_dates.append(issue_date)

        # status == "missing" (freshly reconfirmed) -> move to next date.
        d += timedelta(days=1)

    return report


def print_find_next_report(report: FindNextReport) -> None:
    print("\n===== FIND-NEXT-AVAILABLE-ISSUE REPORT =====")
    print(f"Search started: {report.search_started}")
    print(f"Search end (year boundary): {report.search_end}")
    print(f"Dates checked: {report.dates_checked}")
    print(f"Previously-missing dates live-rechecked: {report.previously_missing_rechecked}")
    print(f"Errors encountered: {len(report.error_dates)}")

    if report.outcome == "SUCCESS":
        print(f"\nNext available issue:\n{report.found_date}")
        r = report.result or {}
        year = int(report.found_date[:4])
        issue_id = r.get("issue_id") or f"{config.NEWSPAPER_CODE}_{report.found_date}"
        pages = sorted(r.get("pages", []), key=lambda p: p["page_number"])
        p_dir = manifest_mod.pages_dir(year, issue_id)
        merged = r.get("merged_pdf") or {}

        print(f"\nPage count: {len(pages)}")
        print(f"Page IDs: {[p['page_id'] for p in pages]}")
        print("Raw page paths:")
        for p in pages:
            print(f"  {p_dir / p['local_filename']}  (sha256={p.get('sha256')})")
        print(f"\nMerged PDF path: {merged.get('path')}")
        print(f"Merged PDF SHA256: {merged.get('sha256')}")
        print(f"Merged PDF page count: {merged.get('page_count')}")
        print(f"Validation status: {'VALIDATED (complete)' if r.get('status') == 'complete' else r.get('status')}")

    elif report.outcome == "FOUND_BUT_FAILED":
        r = report.result or {}
        print(f"\nFOUND BUT FAILED:\n{report.found_date}")
        print(f"  status={r.get('status')} pages_downloaded={r.get('pages_downloaded')}/{r.get('pages_expected')}")
        print("  Stopped here (stop-condition C) -- not advancing to the next date.")

    else:
        print(f"\nNext available issue:\nNONE FOUND THROUGH {report.search_end}")

    if report.error_dates:
        print(f"\nError dates encountered along the way ({len(report.error_dates)}):")
        for ed in report.error_dates:
            print(f"  {ed}")
