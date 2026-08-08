"""Unit/mock tests for skrip_downloader.find_next -- --find-next YYYY-MM-DD.

No network access: pipeline.process_date and manifest_mod are mocked, so
these exercise only find_next's own search/stop-condition logic. Run with:

    python -m unittest tests.test_find_next -v
"""
from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from skrip_downloader import find_next


def _manifest(status, **extra):
    return {"status": status, **extra}


class FindNextAvailableIssueTests(unittest.TestCase):
    def setUp(self):
        self.logger = mock.MagicMock()

    @mock.patch("skrip_downloader.find_next.registry")
    @mock.patch("skrip_downloader.find_next.manifest_mod")
    @mock.patch("skrip_downloader.find_next.process_date")
    def test_skips_complete_dates_without_calling_process_date(
        self, mock_process_date, mock_manifest_mod, mock_registry
    ):
        # 01-01 and 01-02 already complete (no network); 01-03 is the first
        # date find_next actually live-checks, and it's available.
        mock_manifest_mod.load_manifest.side_effect = [
            _manifest("complete"),
            _manifest("complete"),
            None,
        ]
        mock_process_date.return_value = {
            "status": "complete", "issue_id": "SKRIP_1923-01-03",
            "pages": [{"page_number": 1, "page_id": 111, "local_filename": "p001.pdf", "sha256": "abc"}],
            "merged_pdf": {"path": "X.pdf", "sha256": "def", "page_count": 1, "bytes": 10},
        }

        report = find_next.find_next_available_issue(date(1923, 1, 1), self.logger)

        self.assertEqual(report.outcome, "SUCCESS")
        self.assertEqual(report.found_date, "1923-01-03")
        self.assertEqual(report.dates_checked, 3)
        self.assertEqual(report.previously_missing_rechecked, 0)
        self.assertEqual(mock_process_date.call_count, 1)
        mock_process_date.assert_called_once_with(3, 1, 1923, self.logger)

    @mock.patch("skrip_downloader.find_next.registry")
    @mock.patch("skrip_downloader.find_next.manifest_mod")
    @mock.patch("skrip_downloader.find_next.process_date")
    def test_previously_missing_is_discarded_and_freshly_rechecked(
        self, mock_process_date, mock_manifest_mod, mock_registry
    ):
        # 1923-01-22 was recorded "missing" by an earlier crawl; find_next
        # must NOT trust that and must live-recheck it via process_date.
        mock_manifest_mod.load_manifest.return_value = _manifest("missing", pages_expected=0, pages=[])
        fake_path = mock.MagicMock()
        mock_manifest_mod.manifest_path.return_value = fake_path
        mock_process_date.return_value = {"status": "complete", "issue_id": "SKRIP_1923-01-22", "pages": [], "merged_pdf": {}}

        report = find_next.find_next_available_issue(date(1923, 1, 22), self.logger)

        fake_path.unlink.assert_called_once_with(missing_ok=True)
        self.assertEqual(report.previously_missing_rechecked, 1)
        self.assertEqual(report.outcome, "SUCCESS")
        self.assertEqual(report.found_date, "1923-01-22")

    @mock.patch("skrip_downloader.find_next.registry")
    @mock.patch("skrip_downloader.find_next.manifest_mod")
    @mock.patch("skrip_downloader.find_next.process_date")
    def test_error_is_not_treated_as_missing_and_search_continues(
        self, mock_process_date, mock_manifest_mod, mock_registry
    ):
        mock_manifest_mod.load_manifest.return_value = None
        mock_process_date.side_effect = [
            {"status": "error", "issue_id": "SKRIP_1923-01-01"},
            {"status": "complete", "issue_id": "SKRIP_1923-01-02", "pages": [], "merged_pdf": {}},
        ]

        report = find_next.find_next_available_issue(date(1923, 1, 1), self.logger)

        self.assertEqual(report.error_dates, ["1923-01-01"])
        self.assertEqual(report.outcome, "SUCCESS")
        self.assertEqual(report.found_date, "1923-01-02")
        self.assertEqual(mock_process_date.call_count, 2)

    @mock.patch("skrip_downloader.find_next.registry")
    @mock.patch("skrip_downloader.find_next.manifest_mod")
    @mock.patch("skrip_downloader.find_next.process_date")
    def test_found_but_failed_stops_immediately_does_not_advance(
        self, mock_process_date, mock_manifest_mod, mock_registry
    ):
        mock_manifest_mod.load_manifest.return_value = None
        mock_process_date.return_value = {
            "status": "partial", "issue_id": "SKRIP_1923-01-01",
            "pages_downloaded": 1, "pages_expected": 3,
        }

        report = find_next.find_next_available_issue(date(1923, 1, 1), self.logger)

        self.assertEqual(report.outcome, "FOUND_BUT_FAILED")
        self.assertEqual(report.found_date, "1923-01-01")
        self.assertEqual(mock_process_date.call_count, 1)  # must not try the next date

    @mock.patch("skrip_downloader.find_next.registry")
    @mock.patch("skrip_downloader.find_next.manifest_mod")
    @mock.patch("skrip_downloader.find_next.process_date")
    def test_not_found_through_year_end(self, mock_process_date, mock_manifest_mod, mock_registry):
        mock_manifest_mod.load_manifest.return_value = None
        mock_process_date.return_value = {"status": "missing", "issue_id": "x"}

        report = find_next.find_next_available_issue(date(1923, 12, 29), self.logger)

        self.assertEqual(report.outcome, "NOT_FOUND")
        self.assertIsNone(report.found_date)
        self.assertEqual(report.dates_checked, 3)  # 12-29, 12-30, 12-31
        self.assertEqual(report.search_end, "1923-12-31")
        self.assertEqual(mock_process_date.call_count, 3)

    @mock.patch("skrip_downloader.find_next.registry")
    @mock.patch("skrip_downloader.find_next.manifest_mod")
    @mock.patch("skrip_downloader.find_next.process_date")
    def test_never_hardcodes_1923_search_end_follows_start_year(
        self, mock_process_date, mock_manifest_mod, mock_registry
    ):
        mock_manifest_mod.load_manifest.return_value = None
        mock_process_date.return_value = {"status": "missing", "issue_id": "x"}

        report = find_next.find_next_available_issue(date(1930, 12, 30), self.logger)

        self.assertEqual(report.search_end, "1930-12-31")
        self.assertEqual(report.dates_checked, 2)

    @mock.patch("skrip_downloader.find_next.process_date")
    def test_unhandled_exception_becomes_error_not_missing(self, mock_process_date):
        mock_process_date.side_effect = RuntimeError("boom")
        with mock.patch("skrip_downloader.find_next.registry") as mock_registry:
            result = find_next._safe_process_date(date(1923, 1, 1), self.logger)
        self.assertEqual(result["status"], "error")
        mock_registry.update_registry_entry.assert_called_once()
        kwargs = mock_registry.update_registry_entry.call_args.kwargs
        self.assertEqual(kwargs["status"], "error")


class PrintFindNextReportTests(unittest.TestCase):
    """Smoke tests: the report printer must not raise for any outcome, and
    must not require a real filesystem (manifest_mod.pages_dir is mocked)."""

    @mock.patch("skrip_downloader.find_next.manifest_mod")
    def test_prints_success_report_without_error(self, mock_manifest_mod):
        mock_manifest_mod.pages_dir.return_value = mock.MagicMock()
        report = find_next.FindNextReport(
            search_started="1923-01-22", search_end="1923-12-31",
            dates_checked=1, previously_missing_rechecked=1,
            outcome="SUCCESS", found_date="1923-01-22",
            result={
                "issue_id": "SKRIP_1923-01-22",
                "pages": [{"page_number": 1, "page_id": 1, "local_filename": "a.pdf", "sha256": "x"}],
                "merged_pdf": {"path": "m.pdf", "sha256": "y", "page_count": 1},
                "status": "complete",
            },
        )
        find_next.print_find_next_report(report)  # must not raise

    def test_prints_not_found_report_without_error(self):
        report = find_next.FindNextReport(
            search_started="1923-01-22", search_end="1923-12-31", dates_checked=344,
        )
        find_next.print_find_next_report(report)  # must not raise

    def test_prints_found_but_failed_report_without_error(self):
        report = find_next.FindNextReport(
            search_started="1923-01-22", search_end="1923-12-31", dates_checked=1,
            outcome="FOUND_BUT_FAILED", found_date="1923-01-22",
            result={"status": "partial", "pages_downloaded": 1, "pages_expected": 4},
        )
        find_next.print_find_next_report(report)  # must not raise


if __name__ == "__main__":
    unittest.main()
