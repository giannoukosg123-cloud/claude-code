"""Duplicate/near-duplicate content diagnostic -- item=36894 seg=4219,
logical positions 1..20 ONLY. Diagnostic-only: NO bulk, NO OCR, NO merge,
NO changes to the main manifest.

Triggered by a real finding: the sanity test on this segment found two of
the 5 downloaded PDFs corresponding to the same real newspaper page 3 --
so "duplicate current_ids: none" in the main run's report is NOT a
sufficient duplicate check (two different, valid current_ids can still
serve the exact same scanned page, or two near-identical scans of it).

For each of positions 1..20 (using the confirmed header.asp mapping --
current_id is NEVER guessed arithmetically):
  1. Download main.asp?current=<ID> to a dedicated diagnostic folder.
  2. Validate: HTTP 200, %PDF signature, opens with pypdf, page_count >= 1.
  3. sha256 of the raw PDF bytes (exact-file duplicate signal).
  4. Render the first/only page to an image at a FIXED DPI (independent of
     whatever page-size metadata the PDF itself carries) via PyMuPDF.
  5. sha256 of that rendered image's raw pixel bytes (exact-CONTENT
     duplicate signal -- catches two PDFs with different container bytes
     but pixel-identical scans, e.g. re-saved/re-compressed).
  6. A perceptual hash (pHash, 64-bit) of that same rendered image
     (near-duplicate signal -- catches scans of the same page that aren't
     byte-identical, e.g. different compression/crop/slight skew).

Three duplicate tiers are reported, most-to-least strict:
  - exact_pdf_duplicate_groups: identical raw PDF bytes.
  - exact_content_duplicate_groups: different PDF bytes, pixel-identical
    rendered image. This is the case the task description specifically
    calls out ("διαφορετικό SHA256 αλλά ίδια σαρωμένη εικόνα").
  - near_duplicate_groups: different rendered image, but pHash Hamming
    distance <= NEAR_DUP_HAMMING_THRESHOLD (excludes pairs already caught
    by the stricter tiers above, so nothing is double-reported).

Usage:
    pip install pymupdf imagehash Pillow
    python -m parliament_downloader.diagnostic_dedup_36894
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from typing import Any, Dict, List

import imagehash
import pymupdf
from PIL import Image

from . import config, discovery, http_client, pipeline
from .logging_setup import setup_logger, log_event
from .validation import sha256_of_file, validate_pdf_file

RECORD = config.RECORDS[(36894, 4219)]

START_POSITION = 1
END_POSITION = 20  # inclusive

RENDER_DPI = 150
NEAR_DUP_HAMMING_THRESHOLD = 5  # out of 64 bits -- standard pHash near-duplicate cutoff

DIAGNOSTIC_DIR = (
    config.REPO_ROOT / "diagnostic" / f"item_{RECORD.item}_seg_{RECORD.seg}_dedup_positions_{START_POSITION}_{END_POSITION}"
)
REPORT_JSON_PATH = DIAGNOSTIC_DIR / f"dedup_report_item{RECORD.item}_seg{RECORD.seg}_positions_{START_POSITION}_{END_POSITION}.json"


def diag_filename(logical_position: int, current_id: int) -> str:
    return f"diag_pos_{logical_position:04d}_current_{current_id}.pdf"


def sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def render_first_page(pdf_path) -> Image.Image:
    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[0]
        pixmap = page.get_pixmap(dpi=RENDER_DPI)
        png_bytes = pixmap.tobytes("png")
    finally:
        doc.close()
    return Image.open(io.BytesIO(png_bytes)).convert("RGB")


def download_one(session, entry: discovery.DiscoveredEntry, referer: str, logger) -> Dict[str, Any]:
    local_path = DIAGNOSTIC_DIR / diag_filename(entry.logical_position, entry.current_id)
    result: Dict[str, Any] = {
        "logical_position": entry.logical_position,
        "current_id": entry.current_id,
        "source_url": discovery.main_url(entry.current_id),
        "local_filename": local_path.name,
        "bytes": None,
        "sha256": None,
        "page_count": None,
        "rendered_image_sha256": None,
        "phash": None,
        "validation_status": "pending",
        "error": None,
    }

    response = http_client.get_with_retry(
        session, result["source_url"], logger=logger, log_label="main.asp-dedup-diag", stream=True, referer=referer,
    )
    if response.status_code != 200:
        result["validation_status"] = f"error_http_{response.status_code}"
        result["error"] = f"HTTP {response.status_code}"
        log_event(
            logger, RECORD, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DEDUP_DIAG_DOWNLOAD_FAIL", source_url=result["source_url"], local_path=str(local_path),
            status="error", extra=f"http_status={response.status_code}",
        )
        return result

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
    result["bytes"] = check.size_bytes
    if not check.ok:
        result["validation_status"] = f"error_{check.error}"
        result["error"] = check.error
        log_event(
            logger, RECORD, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DEDUP_DIAG_VALIDATE_FAIL", source_url=result["source_url"], local_path=str(local_path),
            status="error", extra=f"error={check.error}",
        )
        return result

    result["sha256"] = check.sha256
    result["page_count"] = check.page_count

    try:
        image = render_first_page(local_path)
        image_bytes = image.tobytes()
        result["rendered_image_sha256"] = sha256_bytes(image_bytes)
        result["phash"] = str(imagehash.phash(image, hash_size=8))
        result["validation_status"] = "ok"
        log_event(
            logger, RECORD, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DEDUP_DIAG_OK", source_url=result["source_url"], local_path=str(local_path),
            status="ok", extra=f"sha256={check.sha256} image_sha256={result['rendered_image_sha256']} phash={result['phash']}",
        )
    except Exception as exc:  # noqa: BLE001
        result["validation_status"] = f"error_render_failed"
        result["error"] = str(exc)
        log_event(
            logger, RECORD, logical_position=entry.logical_position, current_id=entry.current_id,
            action="DEDUP_DIAG_RENDER_FAIL", source_url=result["source_url"], local_path=str(local_path),
            status="error", extra=f"error={exc}",
        )

    return result


class UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb

    def groups(self):
        out: Dict[int, List[int]] = {}
        for item in self.parent:
            out.setdefault(self.find(item), []).append(item)
        return [sorted(v) for v in out.values() if len(v) > 1]


def group_by_key(records: List[Dict[str, Any]], key: str) -> List[List[int]]:
    buckets: Dict[Any, List[int]] = {}
    for r in records:
        if r.get(key) is None:
            continue
        buckets.setdefault(r[key], []).append(r["logical_position"])
    return [sorted(v) for v in buckets.values() if len(v) > 1]


def main() -> None:
    logger = setup_logger(RECORD)
    session = http_client.new_session()

    try:
        entries, strategy, header_url = pipeline.run_discovery(session, RECORD, logger)
    except pipeline.DiscoveryError as exc:
        print("DISCOVERY FAILED -- stopping before any diagnostic download:")
        print(str(exc))
        sys.exit(1)

    entries_by_position = {e.logical_position: e for e in entries}
    positions = list(range(START_POSITION, END_POSITION + 1))
    missing = [p for p in positions if p not in entries_by_position]
    if missing:
        print(f"FATAL: logical position(s) {missing} not found in discovery. Stopping.")
        sys.exit(1)

    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, Any]] = []
    for pos in positions:
        entry = entries_by_position[pos]
        result = download_one(session, entry, referer=header_url, logger=logger)
        records.append(result)
        print(f"pos={pos:>3} current_id={entry.current_id} status={result['validation_status']} "
              f"sha256={(result['sha256'] or '-')[:12]} phash={result['phash'] or '-'}")

    valid_records = [r for r in records if r["validation_status"] == "ok"]

    exact_pdf_groups = group_by_key(valid_records, "sha256")
    exact_content_groups_all = group_by_key(valid_records, "rendered_image_sha256")
    # Only report as "exact_content" the groups that are NOT already fully
    # covered by an exact_pdf group (i.e. genuinely different PDF bytes).
    exact_pdf_group_sets = [set(g) for g in exact_pdf_groups]
    exact_content_groups = [
        g for g in exact_content_groups_all if set(g) not in exact_pdf_group_sets
    ]

    by_position = {r["logical_position"]: r for r in valid_records}
    already_covered_pairs = set()
    for g in exact_pdf_groups + exact_content_groups:
        for i in g:
            for j in g:
                if i != j:
                    already_covered_pairs.add((i, j))

    uf = UnionFind([r["logical_position"] for r in valid_records])
    positions_list = [r["logical_position"] for r in valid_records]
    pairwise_distances: List[Dict[str, Any]] = []
    for a_idx in range(len(positions_list)):
        for b_idx in range(a_idx + 1, len(positions_list)):
            pa, pb = positions_list[a_idx], positions_list[b_idx]
            if (pa, pb) in already_covered_pairs:
                continue
            ha = imagehash.hex_to_hash(by_position[pa]["phash"])
            hb = imagehash.hex_to_hash(by_position[pb]["phash"])
            distance = int(ha - hb)  # imagehash returns a numpy int, not JSON-serializable as-is
            pairwise_distances.append({"positions": [pa, pb], "phash_hamming_distance": distance})
            if distance <= NEAR_DUP_HAMMING_THRESHOLD:
                uf.union(pa, pb)
    near_duplicate_groups = uf.groups()
    # Full transparency, not just the threshold's pass/fail verdict: every
    # pairwise distance among non-exact-duplicate scans, closest first, so
    # a human can sanity-check whether NEAR_DUP_HAMMING_THRESHOLD is
    # actually appropriate for this sample rather than trusting it blindly.
    pairwise_distances.sort(key=lambda d: d["phash_hamming_distance"])

    report = {
        "item_id": RECORD.item,
        "segment_id": RECORD.seg,
        "title": RECORD.title,
        "positions_checked": positions,
        "render_dpi": RENDER_DPI,
        "near_dup_hamming_threshold": NEAR_DUP_HAMMING_THRESHOLD,
        "scans": records,
        "exact_pdf_duplicate_groups": exact_pdf_groups,
        "exact_content_duplicate_groups": exact_content_groups,
        "near_duplicate_groups": near_duplicate_groups,
        "pairwise_phash_distances_closest_first": pairwise_distances,
    }
    REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== DEDUP DIAGNOSTIC REPORT =====")
    print(f"item_id={RECORD.item} segment_id={RECORD.seg} positions={START_POSITION}..{END_POSITION}")
    print(f"\n{'pos':>4} {'current_id':>12} {'sha256':>10} {'image_sha256':>14} {'phash':>18} status")
    for r in records:
        print(f"{r['logical_position']:>4} {r['current_id']:>12} "
              f"{(r['sha256'] or '-')[:10]:>10} {(r['rendered_image_sha256'] or '-')[:14]:>14} "
              f"{r['phash'] or '-':>18} {r['validation_status']}")

    print(f"\nExact PDF duplicate groups (identical raw file bytes): "
          f"{exact_pdf_groups if exact_pdf_groups else 'none'}")
    print(f"Exact-content duplicate groups (different PDF bytes, identical rendered image -- "
          f"different SHA256 but same scanned page): {exact_content_groups if exact_content_groups else 'none'}")
    print(f"Near-duplicate groups (pHash Hamming distance <= {NEAR_DUP_HAMMING_THRESHOLD}, "
          f"not already exact): {near_duplicate_groups if near_duplicate_groups else 'none'}")

    print(f"\nClosest pairwise pHash distances among non-exact-duplicate scans "
          f"(full list in the report JSON -- use this to judge whether "
          f"threshold={NEAR_DUP_HAMMING_THRESHOLD} is actually appropriate for this sample, "
          f"don't just trust the cutoff blindly):")
    for d in pairwise_distances[:15]:
        print(f"  positions {d['positions']}: distance={d['phash_hamming_distance']}")

    print(f"\ndiagnostic folder: {DIAGNOSTIC_DIR}")
    print(f"report JSON:       {REPORT_JSON_PATH}")
    print("\nNO OCR PERFORMED")
    print("NO MERGE PERFORMED")
    print("NO BULK DOWNLOAD PERFORMED")
    print("Main manifest.json for this record was NOT read or modified.")


if __name__ == "__main__":
    main()
