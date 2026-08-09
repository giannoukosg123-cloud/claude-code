"""Discovery: library.asp -> display_doc.asp -> header.asp, ending with the
full, ordered logical_position -> current_id mapping for this segment.

header.asp is the ONLY source of truth for current IDs. They are NEVER
generated arithmetically (confirmed non-contiguous, with large jumps) --
every current_id used anywhere in this project is one that was actually
parsed out of header.asp's HTML.

The exact markup of header.asp has not been inspected ahead of time, so
this parses it with three layered strategies, most-specific first, and
reports which one fired:

  1. <option value="main.asp?current=NNNNNNN">POS</option> elements --
     matches the task description's own summary of header.asp's contents.
     logical_position comes from the option's own visible text if it's a
     plain integer (never assumed from list order alone, though the two
     should agree -- disagreement is logged as a warning).
  2. A JS array assignment like link_array[POS] = "main.asp?current=ID";
     (or similar), via regex.
  3. Fallback: every main.asp?current=NNNNNNN occurrence in the raw HTML,
     in document order, with logical_position = 1-based order of
     appearance. This still never invents a current_id -- it only infers
     position from order, which is the same thing the task description's
     own example implies.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import config
from .http_client import get_with_retry

CURRENT_RE = re.compile(r"current=(\d+)")
JS_ARRAY_RE = re.compile(r"\[\s*(\d+)\s*\]\s*=\s*[\"']([^\"']*current=(\d+)[^\"']*)[\"']")


@dataclass(frozen=True)
class DiscoveredEntry:
    logical_position: int
    current_id: int
    source_url: str


def library_url() -> str:
    return f"{config.BASE_URL}{config.LIBRARY_PATH}?item={config.ITEM}"


def display_doc_url() -> str:
    return f"{config.BASE_URL}{config.DISPLAY_DOC_PATH}?item={config.ITEM}&seg={config.SEG}"


def main_url(current_id: int) -> str:
    return f"{config.BASE_URL}{config.MAIN_PATH}?current={current_id}"


def fetch_library(session: requests.Session, logger: logging.Logger) -> requests.Response:
    return get_with_retry(session, library_url(), logger=logger, log_label="library.asp")


def confirm_segment_present(html: str) -> bool:
    """Both the seg id and the human-readable segment label should appear
    somewhere on library.asp -- if not, we may be looking at the wrong
    item/segment entirely."""
    return f"seg={config.SEG}" in html or str(config.SEG) in html


def fetch_display_doc(session: requests.Session, logger: logging.Logger) -> requests.Response:
    return get_with_retry(session, display_doc_url(), logger=logger, log_label="display_doc.asp", referer=library_url())


def find_header_url(display_doc_html: str, base_url: str) -> Optional[str]:
    """The frameset's header frame's src, resolved to an absolute URL."""
    soup = BeautifulSoup(display_doc_html, "html.parser")
    for tag in soup.find_all(["frame", "iframe"]):
        src = tag.get("src", "")
        if "header.asp" in src.lower():
            return urljoin(base_url, src)
    return None


def fetch_header(session: requests.Session, header_url: str, logger: logging.Logger) -> requests.Response:
    return get_with_retry(session, header_url, logger=logger, log_label="header.asp", referer=display_doc_url())


def _parse_via_options(html: str, logger: logging.Logger) -> List[DiscoveredEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries: List[DiscoveredEntry] = []
    for i, opt in enumerate(soup.find_all("option"), start=1):
        value = opt.get("value", "")
        m = CURRENT_RE.search(value)
        if not m:
            continue
        current_id = int(m.group(1))
        text = opt.get_text(strip=True)
        if text.isdigit():
            logical_position = int(text)
            if logical_position != i:
                logger.info(
                    f"DISCOVERY_POSITION_MISMATCH | option_text={logical_position} | "
                    f"document_order={i} | current_id={current_id}"
                )
        else:
            logical_position = i
        entries.append(DiscoveredEntry(logical_position=logical_position, current_id=current_id, source_url=value))
    return entries


def _parse_via_js_array(html: str) -> List[DiscoveredEntry]:
    entries: List[DiscoveredEntry] = []
    for m in JS_ARRAY_RE.finditer(html):
        index = int(m.group(1))
        raw_value = m.group(2)
        current_id = int(m.group(3))
        entries.append(DiscoveredEntry(logical_position=index, current_id=current_id, source_url=raw_value))
    return entries


def _parse_via_order_fallback(html: str) -> List[DiscoveredEntry]:
    entries: List[DiscoveredEntry] = []
    for i, m in enumerate(CURRENT_RE.finditer(html), start=1):
        current_id = int(m.group(1))
        entries.append(DiscoveredEntry(logical_position=i, current_id=current_id, source_url=f"main.asp?current={current_id}"))
    return entries


def parse_header(html: str, logger: logging.Logger) -> tuple[List[DiscoveredEntry], str]:
    """Returns (entries, strategy_name). Tries strategies in order of
    specificity/confidence and stops at the first that yields anything."""
    entries = _parse_via_options(html, logger)
    if entries:
        return entries, "option-elements"

    entries = _parse_via_js_array(html)
    if entries:
        return entries, "js-array-regex"

    entries = _parse_via_order_fallback(html)
    if entries:
        return entries, "current=-order-fallback"

    return [], "none"
