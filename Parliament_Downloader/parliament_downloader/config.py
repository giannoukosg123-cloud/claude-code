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
    # Filename prefix for scan PDFs (e.g. "AKROPOLIS" -> AKROPOLIS_item...).
    newspaper_code: str = "AKROPOLIS"
    # Filename prefix for the log file (e.g. "akropolis" -> akropolis_item...).
    log_prefix: str = "akropolis"
    # None (default) -> the standard data/raw/akropolis/item_X/seg_Y layout
    # under DATA_ROOT/LOG_ROOT. Set to an absolute path to store this
    # record entirely under that path instead (e.g. a specific Desktop
    # folder) -- manifest.json and the log file then live directly in it too.
    custom_root: Optional[pathlib.Path] = None
    # True (default) -> scans go in a "scans" subfolder under record_root.
    # False -> scans are written directly into record_root itself, no
    # nested subfolder (only meaningful together with custom_root).
    scans_in_subfolder: bool = True

    @property
    def record_root(self) -> pathlib.Path:
        if self.custom_root is not None:
            return self.custom_root
        return DATA_ROOT / "raw" / "akropolis" / f"item_{self.item}" / f"seg_{self.seg}"

    @property
    def scans_dir(self) -> pathlib.Path:
        if not self.scans_in_subfolder:
            return self.record_root
        return self.record_root / "scans"

    @property
    def manifest_path(self) -> pathlib.Path:
        return self.record_root / "manifest.json"

    @property
    def log_path(self) -> pathlib.Path:
        filename = f"{self.log_prefix}_item{self.item}_seg{self.seg}.log"
        if self.custom_root is not None:
            return self.record_root / filename
        return LOG_ROOT / filename


RECORDS: Dict[Tuple[int, int], Record] = {
    (40489, 7597): Record(
        item=40489, seg=7597, title="ΑΚΡΟΠΟΛΙΣ",
        segment_label="ΑΚΡΟΠΟΛΙΣ - 1/7/1937 - 30/9/1937", expected_scans=546,
    ),
    (40221, 7340): Record(
        item=40221, seg=7340, title="ΑΚΡΟΠΟΛΙΣ",
        segment_label="ΑΚΡΟΠΟΛΙΣ - 1/10/1937 - 31/12/1937", expected_scans=574,
    ),
    (40482, 7590): Record(
        item=40482, seg=7590, title="ΑΚΡΟΠΟΛΙΣ",
        segment_label="ΑΚΡΟΠΟΛΙΣ - 1/7/1935 - 30/9/1935", expected_scans=456,
    ),
    (40483, 7591): Record(
        item=40483, seg=7591, title="ΑΚΡΟΠΟΛΙΣ",
        segment_label="ΑΚΡΟΠΟΛΙΣ - 1/10/1935 - 31/12/1935", expected_scans=486,
    ),
    (36894, 4219): Record(
        item=36894, seg=4219, title="ΕΣΠΕΡΙΝΗ",
        segment_label="ΕΣΠΕΡΙΝΗ - 1/10/1925 - 31/12/1925", expected_scans=256,
        newspaper_code="ESPERINI", log_prefix="esperini",
        # Explicit per user request: NOT under data/raw/akropolis/ -- stored
        # directly in this Desktop folder, scans with no "scans/" subfolder,
        # manifest.json and the log colocated in the same folder. This
        # string is only meaningful as a real path when the code actually
        # runs on the Windows machine that folder exists on -- pathlib
        # resolves it to WindowsPath there (backslash-separated) and to a
        # single mangled POSIX filename anywhere else, so this record must
        # never be exercised for real outside that machine.
        custom_root=pathlib.Path(r"C:\Users\user\Desktop\Εσπερινή 1925 10 - 12"),
        scans_in_subfolder=False,
    ),
    (43485, 10580): Record(
        item=43485, seg=10580, title="ΑΘΗΝΑΙ",
        segment_label="ΑΘΗΝΑΙ - ΙΑΝ. - ΙΟΥΝ. 1925", expected_scans=682,
        newspaper_code="ATHINAI", log_prefix="athinai",
        # Same custom-root pattern as ΕΣΠΕΡΙΝΗ: NOT under data/raw/akropolis/,
        # stored directly in this Desktop folder, no "scans/" subfolder,
        # manifest.json and the log colocated in the same folder. Only
        # resolves as a real path on the Windows machine that folder exists
        # on -- see the ΕΣΠΕΡΙΝΗ record's comment above for details.
        custom_root=pathlib.Path(r"C:\Users\user\Desktop\Αθήναι 1925 1 - 6"),
        scans_in_subfolder=False,
    ),
    (43441, 10536): Record(
        item=43441, seg=10536, title="ΑΘΗΝΑΪΚΗ",
        segment_label="ΑΘΗΝΑΪΚΗ - ΜΑΪΟΣ - ΑΥΓ. 1925", expected_scans=404,
        newspaper_code="ATHINAIKI", log_prefix="athinaiki",
        # Same custom-root pattern as ΕΣΠΕΡΙΝΗ/ΑΘΗΝΑΙ: NOT under
        # data/raw/akropolis/, stored directly in this Desktop folder, no
        # "scans/" subfolder, manifest.json and the log colocated in the
        # same folder. Only resolves as a real path on the Windows machine
        # that folder exists on -- see the ΕΣΠΕΡΙΝΗ record's comment above.
        custom_root=pathlib.Path(r"C:\Users\user\Desktop\Αθηναϊκή 1925 5 - 8"),
        scans_in_subfolder=False,
    ),
    (43410, 10504): Record(
        item=43410, seg=10504, title="ΑΘΗΝΑΪΚΗ",
        segment_label="ΑΘΗΝΑΪΚΗ - ΣΕΠΤΕΜΒΡΙΟΣ - ΔΕΚΕΜΒΡΙΟΣ 1925", expected_scans=475,
        newspaper_code="ATHINAIKI", log_prefix="athinaiki",
        # Same custom-root pattern as ΕΣΠΕΡΙΝΗ/ΑΘΗΝΑΙ/item=43441: NOT under
        # data/raw/akropolis/, stored directly in this Desktop folder, no
        # "scans/" subfolder, manifest.json and the log colocated in the
        # same folder. Only resolves as a real path on the Windows machine
        # that folder exists on -- see the ΕΣΠΕΡΙΝΗ record's comment above.
        # Fully independent from item=43441/seg=10536 (different Desktop
        # folder, different date range) despite sharing newspaper_code.
        custom_root=pathlib.Path(r"C:\Users\user\Desktop\Αθηναϊκή 1925 9 - 12"),
        scans_in_subfolder=False,
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
