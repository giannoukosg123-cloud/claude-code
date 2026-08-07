"""Static configuration for the SKRIP (ΣΚΡΙΠ) newspaper downloader.

All values here are intentionally conservative: the target site is an old
IIS 6.0 / classic ASP server and must not be hammered with requests.
"""

from __future__ import annotations

import pathlib

# --- Site --------------------------------------------------------------

BASE_URL = "http://efimeris.nlg.gr"
NS_PATH = "/ns"
PDFWIN_PATH = f"{NS_PATH}/pdfwin.asp"
PAGES_PATH = f"{NS_PATH}/pages.asp"
SHOWPDF_PATH = f"{NS_PATH}/showpdf.asp"

# --- Newspaper identity --------------------------------------------------

NEWSPAPER_NAME_GR = "ΣΚΡΙΠ"
NEWSPAPER_CODE = "SKRIP"
NEWSPAPER_ID = 123

# --- Paths ---------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"
RAW_ROOT = DATA_ROOT / "raw" / "skrip"
ISSUES_ROOT = DATA_ROOT / "issues" / "skrip"
LOG_ROOT = REPO_ROOT / "logs" / "skrip"

# --- HTTP / rate limiting -------------------------------------------------

USER_AGENT = (
    "SKRIP-Archive-Research-Bot/1.0 "
    "(+non-commercial research/archival use; sequential, rate-limited)"
)

REQUEST_TIMEOUT_SECONDS = 30

# Polite delay between *any* two outbound requests to the server.
MIN_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 2.0

# Retry policy for transient failures (timeouts, connection errors, 5xx).
MAX_RETRIES = 3
RETRY_BACKOFF_BASE_SECONDS = 2.0

PDF_MAGIC = b"%PDF"
