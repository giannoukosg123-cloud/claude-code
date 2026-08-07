"""CLI entry point.

Usage:
    python3 -m skrip_downloader.run --dates 1922-01-01 1922-01-03

Intentionally requires an explicit list of dates. There is no "run the
whole year" mode yet — that is a deliberate Phase-1 scope limit, not an
oversight.
"""

from __future__ import annotations

import argparse
from datetime import date

from . import config
from .logging_setup import setup_logger
from .pipeline import process_date


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SKRIP (ΣΚΡΙΠ) issue downloader")
    parser.add_argument(
        "--dates",
        nargs="+",
        required=True,
        help="Dates to process, format YYYY-MM-DD (e.g. 1922-01-01 1922-01-03)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    dates = [date.fromisoformat(d) for d in args.dates]
    years = {d.year for d in dates}
    if len(years) != 1:
        raise SystemExit("All --dates must be within the same year for a single run.")
    year = years.pop()

    logger = setup_logger(year)

    results = {}
    for d in sorted(dates):
        print(f"=== Processing {d.isoformat()} ===")
        result = process_date(d.day, d.month, d.year, logger)
        results[d.isoformat()] = result
        print(f"    status={result['status']} pages_downloaded={result.get('pages_downloaded')}/{result.get('pages_expected')}")

    print("\nDone.")


if __name__ == "__main__":
    main()
