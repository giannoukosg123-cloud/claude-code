"""Discovery of an issue's wrapper page and its page_id -> page_number map.

pages.asp is treated as the single source of truth for the mapping between
the displayed page number and the internal page_id. Page ids are NEVER
assumed to be contiguous or predictable from one another.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from . import config
from .http_client import get_with_retry

GETPDF_RE = re.compile(r"GetPdf\(\s*(\d+)\s*\)")


@dataclass(frozen=True)
class DiscoveredPage:
    page_number: int
    page_id: int


def _issue_query(day: int, month: int, year: int) -> dict:
    return {"c": config.NEWSPAPER_ID, "dc": day, "db": month, "da": year}


def wrapper_url(day: int, month: int, year: int) -> str:
    return f"{config.BASE_URL}{config.PDFWIN_PATH}?{urlencode(_issue_query(day, month, year))}"


def pages_url(day: int, month: int, year: int) -> str:
    return f"{config.BASE_URL}{config.PAGES_PATH}?{urlencode(_issue_query(day, month, year))}"


def fetch_wrapper(session: requests.Session, day: int, month: int, year: int, logger: logging.Logger) -> requests.Response:
    url = wrapper_url(day, month, year)
    return get_with_retry(session, url, logger=logger, log_label="pdfwin.asp")


def fetch_pages_html(session: requests.Session, day: int, month: int, year: int, logger: logging.Logger, referer: str) -> requests.Response:
    url = pages_url(day, month, year)
    return get_with_retry(session, url, logger=logger, log_label="pages.asp", referer=referer)


def parse_pages(html: str, logger: logging.Logger) -> List[DiscoveredPage]:
    """Extract (displayed page number -> page_id) pairs from pages.asp HTML.

    Looks for elements like:
        <div id="p39360" class="page" onmouseup="GetPdf(39360)">1</div>

    Returns an empty list if no page elements are found (caller should
    treat that as "no issue for this date", not as an error to guess at).
    """
    soup = BeautifulSoup(html, "html.parser")
    discovered: List[DiscoveredPage] = []

    for tag in soup.find_all(attrs={"onmouseup": True}):
        onmouseup = tag.get("onmouseup", "")
        match = GETPDF_RE.search(onmouseup)
        if not match:
            continue
        page_id = int(match.group(1))

        displayed_text = tag.get_text(strip=True)
        if not displayed_text.isdigit():
            logger.info(
                f"PAGE_PARSE_SKIP | page_id={page_id} | "
                f"reason=non_numeric_displayed_text | text={displayed_text!r}"
            )
            continue

        discovered.append(DiscoveredPage(page_number=int(displayed_text), page_id=page_id))

    discovered.sort(key=lambda p: p.page_number)

    seen_numbers = set()
    deduped: List[DiscoveredPage] = []
    for page in discovered:
        if page.page_number in seen_numbers:
            logger.info(f"PAGE_PARSE_DUPLICATE | page_number={page.page_number} | page_id={page.page_id}")
            continue
        seen_numbers.add(page.page_number)
        deduped.append(page)

    return deduped
