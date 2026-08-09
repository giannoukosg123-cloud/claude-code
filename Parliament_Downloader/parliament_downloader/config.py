"""Static configuration for the Parliament_Downloader tool.

Independent project. Does NOT import from, depend on, or share any code
with skrip_downloader or akropolis_downloader -- different site
(digitallib.parliament.gr), different confirmed mechanism, no OCR, no
Excel, no NRA integration, no merge/grouping-by-issue.

Multiple item/segment records are supported side by side (see Record /
RECORDS below), each fully isolated: its own data/raw/.../scans/ folder,
its own manifest.json, its own log file. Adding a new segment means
registering a new Record entry here -- nothing else about an existing
record ever changes.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# --- Site -----------------------------------------------------------------

BASE_URL = "https://digitallib.parliament.gr"
LIBRARY_PATH = "/library.asp"
DISPLAY_DOC_PATH = "/display_doc.asp"
MAIN_PATH = "/main.asp"

# --- Shared paths ------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"
LOG_ROOT = REPO_ROOT / "logs"

# --- HTTP / rate limiting ----------------------------------------------------

USER_AGENT = (
    "Parliament-Downloader-Research-Bot/1.0 "
    "(+non-commercial research/archival use; sequential, rate-limited)"
)

REQUEST_TIMEOUT_SECONDS = 30
MIN_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 2.0
MAX_RETRIES = 3
RETRY_BACKOFF_BASE_SECONDS = 2.0

PDF_MAGIC = b"%PDF"


# --- Records: one per item/segment, fully independent -----------------------

@dataclass(frozen=True)
class Record:
    item: int
    seg: int
    title: str
    segment_label: str
    expected_scans: int

    @property
    def record_root(self) -> pathlib.Path:
        return DATA_ROOT / "raw" / "akropolis" / f"item_{self.item}" / f"seg_{self.seg}"

    @property
    def scans_dir(self) -> pathlib.Path:
        return self.record_root / "scans"

    @property
    def manifest_path(self) -> pathlib.Path:
        return self.record_root / "manifest.json"

    @property
    def log_path(self) -> pathlib.Path:
        return LOG_ROOT / f"akropolis_item{self.item}_seg{self.seg}.log"


RECORDS: Dict[Tuple[int, int], Record] = {
    (40489, 7597): Record(
        item=40489, seg=7597, title="ΑΚΡΟΠΟΛΙΣ",
        segment_label="ΑΚΡΟΠΟΛΙΣ - 1/7/1937 - 30/9/1937", expected_scans=546,
    ),
    (40221, 7340): Record(
        item=40221, seg=7340, title="ΑΚΡΟΠΟΛΙΣ",
        segment_label="ΑΚΡΟΠΟΛΙΣ - 1/10/1937 - 31/12/1937", expected_scans=574,
    ),
}

DEFAULT_RECORD = RECORDS[(40489, 7597)]


def get_record(item: Optional[int], seg: Optional[int]) -> Record:
    """Resolve which item/segment to operate on. Both None -> the original
    default record (item=40489, seg=7597), for full backward compatibility
    with every existing invocation that doesn't pass --item/--seg."""
    if item is None and seg is None:
        return DEFAULT_RECORD
    if item is None or seg is None:
        raise ValueError("--item and --seg must be given together.")
    key = (item, seg)
    if key not in RECORDS:
        known = ", ".join(f"item={i} seg={s}" for i, s in sorted(RECORDS))
        raise ValueError(
            f"No registered record for item={item} seg={seg}. "
            f"Register it in parliament_downloader/config.py's RECORDS first. Known records: {known}"
        )
    return RECORDS[key]


# --- Backward-compatible module-level aliases for the default record --------
# metadata_recon.py and diagnostic_range.py (item=40489/seg=7597-only tools)
# still use these directly and are UNCHANGED by the addition of a second
# record -- they only ever operate on the original segment.

ITEM = DEFAULT_RECORD.item
SEG = DEFAULT_RECORD.seg
TITLE = DEFAULT_RECORD.title
SEGMENT_LABEL = DEFAULT_RECORD.segment_label
EXPECTED_SCANS = DEFAULT_RECORD.expected_scans
RECORD_ROOT = DEFAULT_RECORD.record_root
SCANS_DIR = DEFAULT_RECORD.scans_dir
MANIFEST_PATH = DEFAULT_RECORD.manifest_path
LOG_PATH = DEFAULT_RECORD.log_path
