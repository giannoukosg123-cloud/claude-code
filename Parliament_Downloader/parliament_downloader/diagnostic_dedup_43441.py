"""Duplicate-content + current_id-jump reconnaissance -- item=43441
seg=10536 (ΑΘΗΝΑΪΚΗ), logical positions 1..20 AND 190..210 (41 probes
total). Diagnostic-only: NO bulk, NO OCR, NO merge, NO Excel, NO changes
to the record's real manifest.json (which lives in the custom Desktop
root, never touched here).

Triggered by a real finding: visual inspection of the 5-position sanity
test (1, 2, 3, 403, 404) found real pages 4 and 5 downloaded TWICE, and
the next probed page already showed August content -- so logical_position
1..404 cannot yet be assumed to be a safe, continuous chronological
sequence. This does NOT attempt to fix that; it only gathers evidence
toward answering:
  A) Does the Parliament archive genuinely have duplicate scans?
  B) Does the header.asp mapping repeat/shuffle scans?
  C) Are there distinct chronological groups packed inside these 404
     positions?

Writes exclusively to a project-local diagnostic folder -- NEVER to the
record's real custom_root (C:\\Users\\user\\Desktop\\Αθηναϊκή 1925 5 - 8):

    Parliament_Downloader/diagnostic/item_43441_seg_10536_dedup_positions_1_20_and_190_210/

For each of the 41 positions (confirmed header.asp mapping only --
current_id never guessed arithmetically):
  1. Download main.asp?current=<ID>.
  2. Validate: HTTP 200, %PDF signature, opens with pypdf, page_count >= 1.
  3. sha256 of the raw PDF bytes (exact-file duplicate signal).
  4. Render the first/only page to an image at a FIXED DPI via PyMuPDF.
  5. sha256 of that rendered image's pixels (exact-content duplicate
     signal -- different PDF bytes, identical scanned page).
  6. A 64-bit pHash of that image (near-duplicate signal).

Reports, most-to-least strict, each excluding pairs already covered by a
stricter tier:
  - exact_pdf_duplicate_groups
  - exact_content_duplicate_groups
  - near_duplicate_groups (pHash Hamming distance <= NEAR_DUP_HAMMING_THRESHOLD)
  - full pairwise pHash distances for every non-exact pair, closest first
    (not just the threshold's verdict -- for manual calibration)

Also reports current_id "jumps" between TRULY adjacent logical positions
in the sample (i.e. position N and N+1 both present -- within each of the
two contiguous ranges, never across the 21..189 gap between them), so the
non-contiguity already known from earlier segments can be seen concretely
for this one too.

Usage:
    pip install pymupdf imagehash Pillow
    python -m parliament_downloader.diagnostic_dedup_43441
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import sys
import tempfile
from typing import Any, Dict, List

import imagehash
import pymupdf
from PIL import Image

from . import config, discovery, http_client, pipeline
from .logging_setup import log_event
from .validation import validate_pdf_file

RECORD = config.RECORDS[(43441, 10536)]

POSITION_RANGES = [(1, 20), (190, 210)]  # inclusive; 20 + 21 = 41 positions
POSITIONS = [p for start, end in POSITION_RANGES for p in range(start, end + 1)]

RENDER_DPI = 150
NEAR_DUP_HAMMING_THRESHOLD = 5  # out of 64 bits -- standard pHash near-duplicate cutoff

DIAGNOSTIC_DIR = (
    config.REPO_ROOT / "diagnostic" / f"item_{RECORD.item}_seg_{RECORD.seg}_dedup_positions_1_20_and_190_210"
)
REPORT_JSON_PATH = DIAGNOSTIC_DIR / f"dedup_report_item{RECORD.item}_seg{RECORD.seg}_positions_1_20_and_190_210.json"
DIAGNOSTIC_LOG_PATH = DIAGNOSTIC_DIR / f"diagnostic_item{RECORD.item}_seg{RECORD.seg}.log"


def setup_diagnostic_logger(log_path) -> logging.Logger:
    """A logger scoped entirely to this diagnostic run, writing inside
    DIAGNOSTIC_DIR -- deliberately NOT record.log_path, which for a
    custom_root record (like this one) points into the REAL Desktop
    folder. Using the record's real logger here would silently write a
    log file into that real folder, exactly the contamination this
    diagnostic must avoid."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"parliament_downloader.diagnostic.item{RECORD.item}.seg{RECORD.seg}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        formatter = logging.Formatter("%(message)s")
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    return logger


def diag_filename(logical_position: int, current_id: int) -> str:
    return f"diag_pos_{logical_position:04d}_current_{current_id}.pdf"


def sha256_bytes(data: bytes) -> str:
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
        result["validation_status"] = "error_render_failed"
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


def compute_current_id_jumps(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Jumps between TRULY adjacent logical positions (N, N+1) present in
    the sample -- never across the 21..189 gap between the two ranges."""
    by_position = {r["logical_position"]: r for r in records if r["current_id"] is not None}
    jumps = []
    for pos in sorted(by_position):
        nxt = pos + 1
        if nxt in by_position:
            delta = by_position[nxt]["current_id"] - by_position[pos]["current_id"]
            jumps.append({
                "from_position": pos, "to_position": nxt,
                "from_current_id": by_position[pos]["current_id"],
                "to_current_id": by_position[nxt]["current_id"],
                "delta": delta,
            })
    return jumps


def main() -> None:
    logger = setup_diagnostic_logger(DIAGNOSTIC_LOG_PATH)
    session = http_client.new_session()

    try:
        entries, strategy, header_url = pipeline.run_discovery(session, RECORD, logger)
    except pipeline.DiscoveryError as exc:
        print("DISCOVERY FAILED -- stopping before any diagnostic download:")
        print(str(exc))
        sys.exit(1)

    entries_by_position = {e.logical_position: e for e in entries}
    missing = [p for p in POSITIONS if p not in entries_by_position]
    if missing:
        print(f"FATAL: logical position(s) {missing} not found in discovery. Stopping.")
        sys.exit(1)

    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, Any]] = []
    for pos in POSITIONS:
        entry = entries_by_position[pos]
        result = download_one(session, entry, referer=header_url, logger=logger)
        records.append(result)
        print(f"pos={pos:>4} current_id={entry.current_id:>10} status={result['validation_status']} "
              f"sha256={(result['sha256'] or '-')[:12]} phash={result['phash'] or '-'}")

    valid_records = [r for r in records if r["validation_status"] == "ok"]

    exact_pdf_groups = group_by_key(valid_records, "sha256")
    exact_content_groups_all = group_by_key(valid_records, "rendered_image_sha256")
    exact_pdf_group_sets = [set(g) for g in exact_pdf_groups]
    exact_content_groups = [g for g in exact_content_groups_all if set(g) not in exact_pdf_group_sets]

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
            distance = int(ha - hb)
            pairwise_distances.append({"positions": [pa, pb], "phash_hamming_distance": distance})
            if distance <= NEAR_DUP_HAMMING_THRESHOLD:
                uf.union(pa, pb)
    near_duplicate_groups = uf.groups()
    pairwise_distances.sort(key=lambda d: d["phash_hamming_distance"])

    current_id_jumps = compute_current_id_jumps(records)
    non_plus_one_jumps = [j for j in current_id_jumps if j["delta"] != 1]

    report = {
        "item_id": RECORD.item,
        "segment_id": RECORD.seg,
        "title": RECORD.title,
        "position_ranges_checked": POSITION_RANGES,
        "positions_checked": POSITIONS,
        "render_dpi": RENDER_DPI,
        "near_dup_hamming_threshold": NEAR_DUP_HAMMING_THRESHOLD,
        "scans": records,
        "exact_pdf_duplicate_groups": exact_pdf_groups,
        "exact_content_duplicate_groups": exact_content_groups,
        "near_duplicate_groups": near_duplicate_groups,
        "pairwise_phash_distances_closest_first": pairwise_distances,
        "current_id_jumps_between_adjacent_positions": current_id_jumps,
    }
    REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== DEDUP + JUMP DIAGNOSTIC REPORT =====")
    print(f"item_id={RECORD.item} segment_id={RECORD.seg} position_ranges={POSITION_RANGES}")
    print(f"\n{'pos':>4} {'current_id':>10} {'sha256':>10} {'image_sha256':>14} {'phash':>18} status")
    for r in records:
        print(f"{r['logical_position']:>4} {r['current_id']:>10} "
              f"{(r['sha256'] or '-')[:10]:>10} {(r['rendered_image_sha256'] or '-')[:14]:>14} "
              f"{r['phash'] or '-':>18} {r['validation_status']}")

    print(f"\nExact PDF duplicate groups (identical raw file bytes): "
          f"{exact_pdf_groups if exact_pdf_groups else 'none'}")
    print(f"Exact-content duplicate groups (different PDF bytes, identical rendered image): "
          f"{exact_content_groups if exact_content_groups else 'none'}")
    print(f"Near-duplicate groups (pHash Hamming distance <= {NEAR_DUP_HAMMING_THRESHOLD}, not already exact): "
          f"{near_duplicate_groups if near_duplicate_groups else 'none'}")

    print(f"\nClosest pairwise pHash distances among non-exact-duplicate scans "
          f"(full list in the report JSON; use to judge whether "
          f"threshold={NEAR_DUP_HAMMING_THRESHOLD} fits this sample, don't trust it blindly):")
    for d in pairwise_distances[:15]:
        print(f"  positions {d['positions']}: distance={d['phash_hamming_distance']}")

    print(f"\ncurrent_id jumps between truly adjacent logical positions ({len(current_id_jumps)} pairs checked):")
    for j in current_id_jumps:
        flag = "" if j["delta"] == 1 else "  <-- NOT +1"
        print(f"  pos {j['from_position']}->{j['to_position']}: "
              f"current_id {j['from_current_id']}->{j['to_current_id']} (delta={j['delta']}){flag}")
    print(f"\n{len(non_plus_one_jumps)}/{len(current_id_jumps)} adjacent-position jumps are NOT a simple +1.")

    print(f"\ndiagnostic folder: {DIAGNOSTIC_DIR}")
    print(f"report JSON:       {REPORT_JSON_PATH}")
    print(f"diagnostic log:    {DIAGNOSTIC_LOG_PATH}")
    print("\nNO OCR PERFORMED")
    print("NO MERGE PERFORMED")
    print("NO BULK DOWNLOAD PERFORMED")
    print("NO EXCEL CREATED")
    print("NO NRA INTEGRATION")
    print(f"This record's real manifest.json ({RECORD.manifest_path}) was NOT read or modified.")


if __name__ == "__main__":
    main()
