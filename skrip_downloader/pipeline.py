"""End-to-end pipeline for a single issue date.

DISCOVER ISSUE -> DISCOVER PAGES -> DOWNLOAD+VALIDATE PAGE 1..N ->
VERIFY COMPLETENESS -> MERGE -> VALIDATE MERGED -> MARK COMPLETE

One date at a time, one page at a time. No concurrency.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from . import config, discovery, download, http_client, manifest as manifest_mod, merge, registry
from .context import DownloadContext
from .logging_setup import log_event
from .validation import validate_pdf_file, sha256_of_file


def _issue_id(issue_date: str) -> str:
    return f"{config.NEWSPAPER_CODE}_{issue_date}"


def _reuse_or_create_manifest(
    year: int,
    issue_id: str,
    issue_date: str,
    wrapper_url: str,
    pages_url: str,
    discovered,
) -> Dict[str, Any]:
    existing = manifest_mod.load_manifest(year, issue_id)
    expected = [{"page_number": p.page_number, "page_id": p.page_id} for p in discovered]

    if existing and existing.get("status") not in ("missing",):
        existing_keys = [(p["page_number"], p["page_id"]) for p in existing.get("pages", [])]
        new_keys = [(p["page_number"], p["page_id"]) for p in expected]
        if existing_keys == new_keys:
            return existing

    return manifest_mod.new_manifest(
        issue_date=issue_date,
        issue_id=issue_id,
        wrapper_url=wrapper_url,
        pages_url=pages_url,
        pages_expected=expected,
    )


def process_date(day: int, month: int, year: int, logger: logging.Logger) -> Dict[str, Any]:
    issue_date = f"{year:04d}-{month:02d}-{day:02d}"
    issue_id = _issue_id(issue_date)

    manifest_mod.issue_dir(year, issue_id).mkdir(parents=True, exist_ok=True)
    manifest_mod.pages_dir(year, issue_id).mkdir(parents=True, exist_ok=True)

    existing = manifest_mod.load_manifest(year, issue_id)
    if existing and existing.get("status") == "complete":
        merged_info = existing.get("merged_pdf") or {}
        merged_path = merge.merged_pdf_path(year, issue_id)
        if merged_path.exists() and merged_info.get("sha256") == sha256_of_file(merged_path):
            log_event(
                logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
                action="SKIP_COMPLETE", source_url=None, local_path=str(merged_path), status="skipped",
            )
            registry.update_registry_entry(
                year, issue_date, issue_id=issue_id, status="complete",
                pages_expected=existing["pages_expected"], pages_downloaded=existing["pages_downloaded"],
                merged_pdf_path=str(merged_path), error=None,
            )
            return existing

    if existing and existing.get("status") == "missing":
        log_event(
            logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
            action="SKIP_MISSING", source_url=None, local_path=None, status="skipped",
        )
        registry.update_registry_entry(
            year, issue_date, issue_id=issue_id, status="missing",
            pages_expected=0, pages_downloaded=0, merged_pdf_path=None, error=None,
        )
        return existing

    session = http_client.new_session()

    w_url = discovery.wrapper_url(day, month, year)
    p_url = discovery.pages_url(day, month, year)

    wrapper_resp = discovery.fetch_wrapper(session, day, month, year, logger)
    log_event(
        logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
        action="FETCH_WRAPPER", source_url=w_url, local_path=None,
        status=str(wrapper_resp.status_code),
        extra=f"final_url={wrapper_resp.url}",
    )

    # A non-200 here is a real fetch failure (network block, server error,
    # WAF, etc.), NOT evidence that no issue was published that day. It must
    # never be recorded as status="missing" -- that would permanently and
    # incorrectly mark a real issue date as having no newspaper. No
    # manifest.json is written; the date is simply left unresolved so a
    # later run retries it from scratch.
    if wrapper_resp.status_code != 200:
        log_event(
            logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
            action="FETCH_WRAPPER_ERROR", source_url=w_url, local_path=None,
            status="error", extra=f"http_status={wrapper_resp.status_code} body_snippet={wrapper_resp.text[:200]!r}",
        )
        registry.update_registry_entry(
            year, issue_date, issue_id=issue_id, status="error",
            pages_expected=0, pages_downloaded=0, merged_pdf_path=None,
            error=f"pdfwin.asp returned HTTP {wrapper_resp.status_code} (not a valid 'no issue' signal)",
        )
        return {"status": "error", "issue_id": issue_id, "pages_expected": 0, "pages_downloaded": 0}

    pages_resp = discovery.fetch_pages_html(session, day, month, year, logger, referer=w_url)
    log_event(
        logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
        action="FETCH_PAGES", source_url=p_url, local_path=None,
        status=str(pages_resp.status_code),
    )

    if pages_resp.status_code != 200:
        log_event(
            logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
            action="FETCH_PAGES_ERROR", source_url=p_url, local_path=None,
            status="error", extra=f"http_status={pages_resp.status_code} body_snippet={pages_resp.text[:200]!r}",
        )
        registry.update_registry_entry(
            year, issue_date, issue_id=issue_id, status="error",
            pages_expected=0, pages_downloaded=0, merged_pdf_path=None,
            error=f"pages.asp returned HTTP {pages_resp.status_code} (not a valid 'no issue' signal)",
        )
        return {"status": "error", "issue_id": issue_id, "pages_expected": 0, "pages_downloaded": 0}

    discovered = discovery.parse_pages(pages_resp.text, logger)

    if not discovered:
        log_event(
            logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
            action="ISSUE_MISSING", source_url=p_url, local_path=None, status="missing",
        )
        empty_manifest = manifest_mod.new_manifest(
            issue_date=issue_date, issue_id=issue_id, wrapper_url=w_url, pages_url=p_url, pages_expected=[],
        )
        empty_manifest["status"] = "missing"
        manifest_mod.save_manifest(year, issue_id, empty_manifest)
        registry.update_registry_entry(
            year, issue_date, issue_id=issue_id, status="missing",
            pages_expected=0, pages_downloaded=0, merged_pdf_path=None,
            error="no page elements found on pages.asp",
        )
        return empty_manifest

    data = _reuse_or_create_manifest(year, issue_id, issue_date, w_url, p_url, discovered)
    manifest_mod.save_manifest(year, issue_id, data)

    log_event(
        logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
        action="DISCOVER_PAGES", source_url=p_url, local_path=None,
        status="ok", extra=f"pages_expected={len(discovered)} ids={[p.page_id for p in discovered]}",
    )

    base_ctx = DownloadContext.for_issue(issue_date, issue_id)

    for page_entry in sorted(data["pages"], key=lambda p: p["page_number"]):
        page_number = page_entry["page_number"]
        page_id = page_entry["page_id"]
        ctx = base_ctx.for_page(page_number)
        local_filename = f"{issue_id}_p{page_number:03d}.pdf"
        local_path = manifest_mod.pages_dir(year, issue_id) / local_filename

        if page_entry.get("status") == "ok" and local_path.exists():
            check = validate_pdf_file(local_path)
            if check.ok and check.sha256 == page_entry.get("sha256"):
                log_event(
                    logger, issue_id=issue_id, issue_date=issue_date, page_number=page_number,
                    action="SKIP_ALREADY_DONE", source_url=page_entry.get("resolved_pdf_url"),
                    local_path=str(local_path), status="skipped",
                )
                continue
            log_event(
                logger, issue_id=issue_id, issue_date=issue_date, page_number=page_number,
                action="RESUME_REVALIDATE_FAILED", source_url=None, local_path=str(local_path),
                status="failed", extra=f"error={check.error}",
            )

        try:
            resolved = download.resolve_pdf_url(session, page_id, ctx, logger, referer=p_url)
            log_event(
                logger, issue_id=issue_id, issue_date=issue_date, page_number=page_number,
                action="RESOLVE_OK", source_url=resolved.showpdf_url, local_path=None,
                status="ok", extra=f"resolved_pdf_url={resolved.resolved_pdf_url}",
            )

            download.download_pdf_bytes(session, resolved, local_path, ctx, logger, referer=p_url)

            check = validate_pdf_file(local_path)
            if not check.ok:
                log_event(
                    logger, issue_id=issue_id, issue_date=issue_date, page_number=page_number,
                    action="VALIDATE_FAIL", source_url=resolved.resolved_pdf_url,
                    local_path=str(local_path), status="failed", extra=f"error={check.error}",
                )
                page_entry.update({
                    "showpdf_url": resolved.showpdf_url,
                    "redirect_location": resolved.redirect_location,
                    "resolved_pdf_url": resolved.resolved_pdf_url,
                    "original_filename": resolved.original_filename,
                    "local_filename": local_filename,
                    "bytes": check.size_bytes,
                    "sha256": None,
                    "status": "failed",
                    "error": check.error,
                })
            else:
                log_event(
                    logger, issue_id=issue_id, issue_date=issue_date, page_number=page_number,
                    action="DOWNLOAD_OK", source_url=resolved.resolved_pdf_url,
                    local_path=str(local_path), status="ok",
                    extra=f"bytes={check.size_bytes} sha256={check.sha256}",
                )
                page_entry.update({
                    "showpdf_url": resolved.showpdf_url,
                    "redirect_location": resolved.redirect_location,
                    "resolved_pdf_url": resolved.resolved_pdf_url,
                    "original_filename": resolved.original_filename,
                    "local_filename": local_filename,
                    "bytes": check.size_bytes,
                    "sha256": check.sha256,
                    "status": "ok",
                    "error": None,
                })
        except Exception as exc:  # noqa: BLE001 - any failure -> page marked failed, pipeline continues
            log_event(
                logger, issue_id=issue_id, issue_date=issue_date, page_number=page_number,
                action="DOWNLOAD_FAIL", source_url=None, local_path=str(local_path),
                status="failed", extra=f"error={exc}",
            )
            page_entry.update({"status": "failed", "error": str(exc)})

        manifest_mod.save_manifest(year, issue_id, data)

    ok_pages = [p for p in data["pages"] if p["status"] == "ok"]
    data["pages_downloaded"] = len(ok_pages)

    if len(ok_pages) == data["pages_expected"] and data["pages_expected"] > 0:
        try:
            merged_info = merge.merge_issue(year, issue_id, data)
            data["merged_pdf"] = merged_info
            data["status"] = "complete"
            manifest_mod.save_manifest(year, issue_id, data)
            log_event(
                logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
                action="MERGE_OK", source_url=None, local_path=merged_info["path"], status="ok",
                extra=f"page_count={merged_info['page_count']} sha256={merged_info['sha256']}",
            )
            registry.update_registry_entry(
                year, issue_date, issue_id=issue_id, status="complete",
                pages_expected=data["pages_expected"], pages_downloaded=data["pages_downloaded"],
                merged_pdf_path=merged_info["path"], error=None,
            )
        except merge.MergeError as exc:
            data["status"] = "partial"
            manifest_mod.save_manifest(year, issue_id, data)
            log_event(
                logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
                action="MERGE_FAIL", source_url=None, local_path=None, status="failed",
                extra=f"error={exc}",
            )
            registry.update_registry_entry(
                year, issue_date, issue_id=issue_id, status="partial",
                pages_expected=data["pages_expected"], pages_downloaded=data["pages_downloaded"],
                merged_pdf_path=None, error=str(exc),
            )
    else:
        data["status"] = "partial"
        manifest_mod.save_manifest(year, issue_id, data)
        log_event(
            logger, issue_id=issue_id, issue_date=issue_date, page_number=None,
            action="ISSUE_PARTIAL", source_url=None, local_path=None, status="partial",
            extra=f"{len(ok_pages)}/{data['pages_expected']} pages ok",
        )
        registry.update_registry_entry(
            year, issue_date, issue_id=issue_id, status="partial",
            pages_expected=data["pages_expected"], pages_downloaded=data["pages_downloaded"],
            merged_pdf_path=None, error=f"only {len(ok_pages)}/{data['pages_expected']} pages ok",
        )

    return data
