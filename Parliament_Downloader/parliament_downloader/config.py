"""Static configuration for the Parliament_Downloader tool.

Independent project. Does NOT import from, depend on, or share any code
with skrip_downloader or akropolis_downloader -- different site
(digitallib.parliament.gr), different confirmed mechanism, no OCR, no
Excel, no NRA integration, no merge/grouping-by-issue.
"""

from __future__ import annotations

import pathlib

# --- Site -----------------------------------------------------------------

BASE_URL = "https://digitallib.parliament.gr"
LIBRARY_PATH = "/library.asp"
DISPLAY_DOC_PATH = "/display_doc.asp"
MAIN_PATH = "/main.asp"

# --- This specific record --------------------------------------------------

ITEM = 40489
SEG = 7597
TITLE = "ΑΚΡΟΠΟΛΙΣ"
SEGMENT_LABEL = "ΑΚΡΟΠΟΛΙΣ - 1/7/1937 - 30/9/1937"
EXPECTED_SCANS = 546

# --- Paths ------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"
RECORD_ROOT = DATA_ROOT / "raw" / "akropolis" / f"item_{ITEM}" / f"seg_{SEG}"
SCANS_DIR = RECORD_ROOT / "scans"
MANIFEST_PATH = RECORD_ROOT / "manifest.json"
LOG_ROOT = REPO_ROOT / "logs"
LOG_PATH = LOG_ROOT / f"akropolis_item{ITEM}_seg{SEG}.log"

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
