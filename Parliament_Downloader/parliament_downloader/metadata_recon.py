"""Metadata reconnaissance -- READ ONLY, NO BULK DOWNLOAD.

Investigates whether digitallib.parliament.gr exposes any machine-readable
way to map a current_id to (issue_date, newspaper_page_number) WITHOUT OCR,
before any bulk download of the 546 AKROPOLIS item=40489/seg=7597 scans.

Strictly scoped to the 5 known test scans (the only ones this project has
downloaded or is allowed to download here):

    logical_position=1   current_id=7157858
    logical_position=2   current_id=7157859
    logical_position=163 current_id=7158020
    logical_position=164 current_id=7158929
    logical_position=546 current_id=7159538

For each, this fetches:
  - header.asp?item=40489&seg=7597&current=<ID>  (once per ID -- header.asp
    takes a current= param, so it may render per-page context; the 5
    responses are diffed against each other to surface anything that
    changes besides the expected <option selected> shift)
  - main.asp?current=<ID>                        (full response: ALL HTTP
    headers, plus PDF document-info metadata via pypdf)

Plus, once each (shared, not per-scan):
  - library.asp?item=40489
  - library.asp?item=40489&seg=7597
  - display_doc.asp?item=40489&seg=7597

Every fetched HTML page is scanned for: hidden inputs, JS array
assignments, data-* attributes, HTML comments, every *.asp endpoint
referenced anywhere, and date-like / issue-like / page-like text (Greek
month names, "Τεύχος", "Αριθ. Φύλλου", "Έτος", "σελ.", plain date
patterns) -- printed with surrounding context so a human can judge
relevance instead of the script guessing.

This does NOT touch skrip_downloader, does NOT touch the already-downloaded
test PDFs, does NOT integrate with NRA, does NOT do OCR, does NOT merge,
does NOT create Excel, and does NOT download any of the other 541 scans.

Usage:
    python -m parliament_downloader.metadata_recon
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from bs4 import BeautifulSoup, Comment
from pypdf import PdfReader

from . import config, http_client
from .discovery import display_doc_url, library_url, main_url

# This recon tool only ever operates on the original default record
# (item=40489, seg=7597).
RECORD = config.DEFAULT_RECORD

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE.parent / "recon_output"

KNOWN_SCANS = [
    {"logical_position": 1, "current_id": 7157858},
    {"logical_position": 2, "current_id": 7157859},
    {"logical_position": 163, "current_id": 7158020},
    {"logical_position": 164, "current_id": 7158929},
    {"logical_position": 546, "current_id": 7159538},
]

ASP_ENDPOINT_RE = re.compile(r"[\w./%-]+\.asp\b[^\"'\s>]*", re.IGNORECASE)
DATA_ATTR_RE = re.compile(r'\bdata-[a-z0-9-]+\s*=\s*"[^"]*"', re.IGNORECASE)

GREEK_MONTHS = (
    r"Ιανουαρ\w*|Φεβρουαρ\w*|Μαρτ\w*|Απριλ\w*|Μα[ΐϊ]ου|Μάιος|Ιουν\w*|Ιουλ\w*|"
    r"Αυγο[υύ]στ\w*|Σεπτεμβρ\w*|Οκτωβρ\w*|Νοεμβρ\w*|Δεκεμβρ\w*"
)
DATE_LIKE_RE = re.compile(
    rf"(\d{{1,2}}\s*[/.\-]\s*\d{{1,2}}\s*[/.\-]\s*\d{{2,4}})|"
    rf"(\d{{1,2}}\s+(?:{GREEK_MONTHS})\s+\d{{4}})|"
    rf"({GREEK_MONTHS}\s+\d{{4}})",
    re.IGNORECASE,
)
ISSUE_KEYWORD_RE = re.compile(
    r"(Τε[υύ]χος|Αριθ\.?\s*Φ[υύ]λλου|Αριθμ[οό]ς\s*Φ[υύ]λλου|΄Ετος|Έτος|σελ\.?\s*\d|σελίδα\s*\d)",
    re.IGNORECASE,
)


def safe_text(resp) -> str:
    """requests defaults to ISO-8859-1 for text/* responses that don't
    declare a charset in Content-Type -- which mangles UTF-8 Greek text
    into mojibake. If no charset was declared, re-decode using chardet's
    content-based guess instead of trusting that default."""
    content_type = resp.headers.get("content-type", "")
    if "charset" not in content_type.lower():
        resp.encoding = resp.apparent_encoding
    return resp.text


def _context(text: str, start: int, end: int, radius: int = 50) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi].replace("\n", " ").strip()


def scan_html(html: str, label: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    hidden_inputs = [
        {"name": t.get("name"), "value": t.get("value")}
        for t in soup.find_all("input", attrs={"type": "hidden"})
    ]

    js_array_assignments = re.findall(r"[\w.]+\[[^\]]+\]\s*=\s*[^;]{1,200};", html)

    data_attrs = sorted(set(DATA_ATTR_RE.findall(html)))

    comments = [c.strip() for c in soup.find_all(string=lambda s: isinstance(s, Comment)) if c.strip()]

    asp_endpoints = sorted(set(ASP_ENDPOINT_RE.findall(html)))

    date_hits = [
        {"match": m.group(0), "context": _context(html, m.start(), m.end())}
        for m in DATE_LIKE_RE.finditer(html)
    ]
    issue_keyword_hits = [
        {"match": m.group(0), "context": _context(html, m.start(), m.end())}
        for m in ISSUE_KEYWORD_RE.finditer(html)
    ]

    return {
        "label": label,
        "hidden_inputs": hidden_inputs,
        "js_array_assignments": js_array_assignments[:50],
        "data_attributes": data_attrs,
        "html_comments": comments,
        "asp_endpoints_referenced": asp_endpoints,
        "date_like_matches": date_hits,
        "issue_keyword_matches": issue_keyword_hits,
    }


def print_scan_result(result: Dict[str, Any]) -> None:
    print(f"\n--- {result['label']} ---")
    print(f"  hidden inputs: {result['hidden_inputs'] or 'none'}")
    print(f"  JS array assignments ({len(result['js_array_assignments'])} shown, capped at 50): "
          f"{result['js_array_assignments'][:5]}{' ...' if len(result['js_array_assignments']) > 5 else ''}")
    print(f"  data-* attributes: {result['data_attributes'] or 'none'}")
    print(f"  HTML comments: {result['html_comments'] or 'none'}")
    print(f"  *.asp endpoints referenced: {result['asp_endpoints_referenced'] or 'none'}")
    if result["date_like_matches"]:
        print(f"  DATE-LIKE matches ({len(result['date_like_matches'])}):")
        for h in result["date_like_matches"][:20]:
            print(f"    {h['match']!r}  context: ...{h['context']}...")
    else:
        print("  DATE-LIKE matches: none")
    if result["issue_keyword_matches"]:
        print(f"  ISSUE-KEYWORD matches ({len(result['issue_keyword_matches'])}):")
        for h in result["issue_keyword_matches"][:20]:
            print(f"    {h['match']!r}  context: ...{h['context']}...")
    else:
        print("  ISSUE-KEYWORD matches: none")


def header_url_for(current_id: int) -> str:
    return f"{config.BASE_URL}/header.asp?item={config.ITEM}&seg={config.SEG}&current={current_id}"


def strip_select_blocks(html: str) -> str:
    """Remove the big 546-option page-selector <select> so the remaining
    text can be diffed across the 5 fetches without the expected
    <option selected> shift drowning out anything actually interesting."""
    soup = BeautifulSoup(html, "lxml")
    for sel in soup.find_all("select"):
        sel.decompose()
    return soup.get_text(separator=" | ", strip=True)


class _NullLogger:
    """Stand-in logger: prints retry/error lines to stdout without needing
    the full logging_setup machinery for this one-off recon script."""

    def info(self, msg: str) -> None:
        print(f"    [http] {msg}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = http_client.new_session()
    logger = _NullLogger()

    all_scan_results: List[Dict[str, Any]] = []

    print("########## SHARED PAGES (fetched once) ##########")
    lib_resp = http_client.get_with_retry(session, library_url(RECORD), logger=logger, log_label="library.asp")
    print(f"library.asp -> HTTP {lib_resp.status_code}")
    r = scan_html(safe_text(lib_resp), "library.asp?item=40489")
    print_scan_result(r)
    all_scan_results.append(r)

    lib_seg_url = f"{library_url(RECORD)}&seg={RECORD.seg}"
    lib_seg_resp = http_client.get_with_retry(session, lib_seg_url, logger=logger, log_label="library.asp+seg")
    print(f"\nlibrary.asp?item=40489&seg=7597 -> HTTP {lib_seg_resp.status_code}")
    r = scan_html(safe_text(lib_seg_resp), "library.asp?item=40489&seg=7597")
    print_scan_result(r)
    all_scan_results.append(r)

    doc_resp = http_client.get_with_retry(session, display_doc_url(RECORD), logger=logger, log_label="display_doc.asp")
    print(f"\ndisplay_doc.asp -> HTTP {doc_resp.status_code}")
    r = scan_html(safe_text(doc_resp), "display_doc.asp?item=40489&seg=7597")
    print_scan_result(r)
    all_scan_results.append(r)

    print("\n\n########## PER-SCAN: header.asp?...&current=<ID> (5 known scans) ##########")
    header_texts: Dict[int, str] = {}
    for scan in KNOWN_SCANS:
        cid = scan["current_id"]
        url = header_url_for(cid)
        resp = http_client.get_with_retry(session, url, logger=logger, log_label="header.asp+current",
                                           referer=display_doc_url(RECORD))
        print(f"\nheader.asp current={cid} (pos={scan['logical_position']}) -> HTTP {resp.status_code}")
        r = scan_html(safe_text(resp), f"header.asp current={cid} (pos={scan['logical_position']})")
        print_scan_result(r)
        all_scan_results.append(r)
        header_texts[cid] = strip_select_blocks(safe_text(resp))

    print("\n\n########## DIFF: header.asp text (minus the page-select) across the 5 scans ##########")
    print("If a date/page label exists in header.asp, it should show up as a difference here.")
    for scan in KNOWN_SCANS:
        cid = scan["current_id"]
        print(f"\n[pos={scan['logical_position']} current={cid}] text (first 500 chars):")
        print(f"  {header_texts[cid][:500]}")

    baseline_cid = KNOWN_SCANS[0]["current_id"]
    baseline_text = header_texts[baseline_cid]
    print(f"\nComparing every other scan's header text against pos=1 (current={baseline_cid}):")
    any_diff = False
    for scan in KNOWN_SCANS[1:]:
        cid = scan["current_id"]
        if header_texts[cid] != baseline_text:
            any_diff = True
            print(f"  pos={scan['logical_position']} current={cid}: DIFFERS from baseline")
        else:
            print(f"  pos={scan['logical_position']} current={cid}: IDENTICAL to baseline "
                  f"(no page-specific text found outside the <select>)")
    if not any_diff:
        print("\n  ==> header.asp's non-select content is byte-identical across all 5 test scans. "
              "No per-page metadata found there.")

    print("\n\n########## PER-SCAN: main.asp?current=<ID> -- HTTP headers + PDF metadata ##########")
    pdf_metadata_by_scan = []
    for scan in KNOWN_SCANS:
        cid = scan["current_id"]
        url = main_url(cid)
        resp = http_client.get_with_retry(session, url, logger=logger, log_label="main.asp", stream=True,
                                           referer=header_url_for(cid))
        print(f"\nmain.asp current={cid} (pos={scan['logical_position']}) -> HTTP {resp.status_code}")
        print("  Response headers:")
        for k, v in resp.headers.items():
            print(f"    {k}: {v}")

        content = resp.content
        pdf_path = OUT_DIR / f"probe_current_{cid}.pdf"
        pdf_path.write_bytes(content)

        doc_info = {}
        try:
            reader = PdfReader(str(pdf_path))
            if reader.metadata:
                doc_info = {k: str(v) for k, v in reader.metadata.items()}
        except Exception as exc:  # noqa: BLE001
            doc_info = {"_error": str(exc)}
        print(f"  PDF DocumentInformation metadata: {doc_info or 'none'}")

        pdf_metadata_by_scan.append({
            "logical_position": scan["logical_position"],
            "current_id": cid,
            "http_headers": dict(resp.headers),
            "pdf_metadata": doc_info,
        })

    print("\n\n########## PDF METADATA COMPARISON ACROSS SCANS ##########")
    all_keys = sorted({k for s in pdf_metadata_by_scan for k in s["pdf_metadata"].keys()})
    if not all_keys:
        print("No PDF DocumentInformation fields present on any of the 5 scans.")
    for key in all_keys:
        values = {s["logical_position"]: s["pdf_metadata"].get(key) for s in pdf_metadata_by_scan}
        varies = len(set(values.values())) > 1
        print(f"  {key}: {'VARIES per scan' if varies else 'same/absent on all'} -> {values}")

    out_json = OUT_DIR / "metadata_recon_results.json"
    out_json.write_text(
        json.dumps(
            {
                "shared_pages": all_scan_results[:3],
                "per_scan_header": all_scan_results[3:],
                "header_text_diff_baseline_pos": 1,
                "pdf_and_http_metadata": pdf_metadata_by_scan,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nFull structured results written to: {out_json}")
    print(f"Probe PDFs (for the 5 known scans only) written to: {OUT_DIR}")
    print("\nNO bulk download performed. NO OCR performed. NO merge performed. NO Excel created.")


if __name__ == "__main__":
    main()
