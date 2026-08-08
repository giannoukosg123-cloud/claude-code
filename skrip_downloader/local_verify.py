"""Offline, read-only verification of already-crawled issues.

Hard invariant: this module must NEVER import http_client, discovery, or
download, and must NEVER call manifest.save_manifest, merge.merge_issue, or
registry.save_registry. Every function here only reads what a previous crawl
already wrote to disk (manifest.json, issues_registry.json, raw page PDFs,
merged PDFs) and reports whether it is internally consistent. It never makes
a network request and never mutates anything.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from . import config, manifest as manifest_mod, merge, registry
from .validation import validate_pdf_file
from .verification import find_cross_contamination, snapshot_merged_pdfs


def _issue_id(year: int, month: int, day: int) -> str:
    return f"{config.NEWSPAPER_CODE}_{year:04d}-{month:02d}-{day:02d}"


def classify_date(year: int, day: int, month: int, registry_entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure read: manifest_mod.load_manifest + validate_pdf_file only."""
    issue_date = f"{year:04d}-{month:02d}-{day:02d}"
    issue_id = _issue_id(year, month, day)
    data = manifest_mod.load_manifest(year, issue_id)
    problems: List[str] = []

    if data is None:
        registry_status = registry_entry.get("status") if registry_entry else None
        if registry_entry is None:
            state = "unresolved"
        elif registry_status == "error":
            state = "error_ok"
        else:
            state = "unresolved_INCONSISTENT"
            problems.append(
                f"no manifest.json on disk, but registry status={registry_status!r} (expected 'error' or no entry)"
            )
        return {"date": issue_date, "issue_id": issue_id, "state": state, "problems": problems}

    status = data.get("status")
    if registry_entry is not None and registry_entry.get("status") != status:
        problems.append(
            f"registry status={registry_entry.get('status')!r} does not match manifest status={status!r}"
        )

    if status == "missing":
        if data.get("pages_expected", 0) != 0 or data.get("pages") or data.get("merged_pdf"):
            problems.append("manifest status=missing but pages_expected/pages/merged_pdf are non-empty")
        state = "missing_ok" if not problems else "missing_INCONSISTENT"
        return {"date": issue_date, "issue_id": issue_id, "state": state, "problems": problems}

    if status == "pending":
        return {"date": issue_date, "issue_id": issue_id, "state": "pending", "problems": problems}

    seen_numbers = set()
    seen_ids = set()
    p_dir = manifest_mod.pages_dir(year, issue_id)
    for page in data.get("pages", []):
        pn, pid = page.get("page_number"), page.get("page_id")
        if pn in seen_numbers:
            problems.append(f"duplicate page_number={pn} in manifest")
        seen_numbers.add(pn)
        if pid in seen_ids:
            problems.append(f"duplicate page_id={pid} in manifest")
        seen_ids.add(pid)

        if page.get("status") != "ok":
            continue
        local_filename = page.get("local_filename")
        if not local_filename:
            problems.append(f"page {pn}: status=ok but no local_filename recorded")
            continue
        page_path = p_dir / local_filename
        check = validate_pdf_file(page_path)
        if not check.ok:
            problems.append(f"page {pn}: {page_path} failed validation ({check.error})")
        elif check.sha256 != page.get("sha256"):
            problems.append(
                f"page {pn}: sha256 mismatch (manifest={page.get('sha256')}, disk={check.sha256})"
            )

    if status == "complete":
        merged_info = data.get("merged_pdf") or {}
        merged_path = merge.merged_pdf_path(year, issue_id)
        check = validate_pdf_file(merged_path)
        if not check.ok:
            problems.append(f"merged PDF {merged_path} failed validation ({check.error})")
        else:
            if check.sha256 != merged_info.get("sha256"):
                problems.append(
                    f"merged PDF sha256 mismatch (manifest={merged_info.get('sha256')}, disk={check.sha256})"
                )
            if check.page_count != merged_info.get("page_count"):
                problems.append(
                    f"merged PDF page_count mismatch (manifest={merged_info.get('page_count')}, disk={check.page_count})"
                )
            if check.page_count != data.get("pages_expected"):
                problems.append(
                    f"merged PDF page_count != pages_expected ({check.page_count} != {data.get('pages_expected')})"
                )
        if data.get("pages_downloaded") != data.get("pages_expected"):
            problems.append(
                f"pages_downloaded ({data.get('pages_downloaded')}) != pages_expected ({data.get('pages_expected')})"
            )
        state = "complete_VERIFIED" if not problems else "complete_BROKEN"
        return {"date": issue_date, "issue_id": issue_id, "state": state, "problems": problems}

    if status == "partial":
        state = "partial_ok" if not problems else "partial_BROKEN"
        return {"date": issue_date, "issue_id": issue_id, "state": state, "problems": problems}

    problems.append(f"unrecognized manifest status={status!r}")
    return {"date": issue_date, "issue_id": issue_id, "state": "unknown", "problems": problems}


def _duplicate_merged_pdf_groups(snapshot: Dict[str, Dict[str, object]]) -> List[List[str]]:
    by_sha: Dict[str, List[str]] = {}
    for issue_id, info in snapshot.items():
        by_sha.setdefault(str(info["sha256"]), []).append(issue_id)
    return [sorted(ids) for ids in by_sha.values() if len(ids) > 1]


def verify_year(year: int, dates: List[date]) -> Dict[str, Any]:
    registry_data = registry.load_registry(year)
    per_date: Dict[str, Dict[str, Any]] = {}
    for d in dates:
        issue_date = d.isoformat()
        entry = registry_data.get(issue_date)
        per_date[issue_date] = classify_date(d.year, d.day, d.month, entry)

    return {
        "year": year,
        "per_date": per_date,
        "contamination_violations": find_cross_contamination(year),
        "duplicate_merged_pdf_groups": _duplicate_merged_pdf_groups(snapshot_merged_pdfs(year)),
    }


def run_local_verification(groups: Dict[int, List[date]]) -> Dict[int, Dict[str, Any]]:
    return {year: verify_year(year, dates) for year, dates in sorted(groups.items())}


def print_verification_report(report: Dict[int, Dict[str, Any]], years: List[int]) -> None:
    from collections import Counter

    print("\n===== LOCAL VERIFICATION REPORT (offline, read-only) =====")

    all_problem_dates: List[str] = []
    total_checked = 0
    for y in years:
        year_report = report[y]
        per_date = year_report["per_date"]
        total_checked += len(per_date)
        counts = Counter(v["state"] for v in per_date.values())
        print(f"\n-- Year {y} -- ({len(per_date)} dates checked)")
        for state in sorted(counts):
            print(f"  {state}: {counts[state]}")

        contamination = year_report["contamination_violations"]
        print(f"  Cross-issue contamination: {'CLEAN' if not contamination else 'VIOLATIONS FOUND'}")
        for v in contamination:
            print(f"    VIOLATION: {v}")

        dup_groups = year_report["duplicate_merged_pdf_groups"]
        print(f"  Duplicate merged-PDF content: {'CLEAN' if not dup_groups else 'VIOLATIONS FOUND'}")
        for group in dup_groups:
            print(f"    DUPLICATE: identical merged PDF content across {group}")

        for issue_date, v in sorted(per_date.items()):
            if v["problems"]:
                all_problem_dates.append(f"{y}:{issue_date}")

    print(f"\nTotal dates checked across all years: {total_checked}")
    if all_problem_dates:
        print(f"\nDates with problems ({len(all_problem_dates)}):")
        for key in all_problem_dates:
            y_str, issue_date = key.split(":", 1)
            v = report[int(y_str)]["per_date"][issue_date]
            print(f"  {issue_date} [{v['state']}]:")
            for p in v["problems"]:
                print(f"      - {p}")
    else:
        print("\nNo problems found in any checked date.")
