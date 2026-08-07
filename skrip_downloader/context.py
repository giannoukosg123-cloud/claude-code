"""Immutable per-download context.

A fresh DownloadContext is created for each page download and threaded
explicitly through every function call. Nothing here is stored in a
module-level/global variable, so there is no risk of one issue's or one
page's identity leaking into another's download (race conditions,
"current_page"-style globals, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import config


@dataclass(frozen=True)
class DownloadContext:
    newspaper: str
    newspaper_id: int
    issue_date: str  # YYYY-MM-DD
    issue_id: str  # SKRIP_YYYY-MM-DD
    page_number: Optional[int] = None

    @staticmethod
    def for_issue(issue_date: str, issue_id: str) -> "DownloadContext":
        return DownloadContext(
            newspaper=config.NEWSPAPER_CODE,
            newspaper_id=config.NEWSPAPER_ID,
            issue_date=issue_date,
            issue_id=issue_id,
            page_number=None,
        )

    def for_page(self, page_number: int) -> "DownloadContext":
        return DownloadContext(
            newspaper=self.newspaper,
            newspaper_id=self.newspaper_id,
            issue_date=self.issue_date,
            issue_id=self.issue_id,
            page_number=page_number,
        )
