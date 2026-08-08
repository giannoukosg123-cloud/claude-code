# SKRIP_Downloader

Downloader/archiver for the Greek newspaper ΣΚΡΙΠ (SKRIP), sourced from the
National Library of Greece's digital newspaper archive
(`http://efimeris.nlg.gr`). One issue = one calendar date. Each issue's pages
are discovered, downloaded, validated, and merged into a single PDF per date.

## CLI

```
python -m skrip_downloader.run --dates 1922-01-01 1922-01-03
python -m skrip_downloader.run --year 1922
python -m skrip_downloader.run --from 1923-01-01 --to 1923-10-31
python -m skrip_downloader.run --verify --from 1923-01-01 --to 1923-10-31
```

- `--dates` — explicit list of dates (must share one year). Single pass, no
  automatic verification.
- `--year` / `--from`+`--to` — crawl every calendar date in the span
  (grouped internally by calendar year so storage/logs/registry never mix
  years), followed automatically by a **local, offline, read-only
  verification pass** (`skrip_downloader/local_verify.py`) — never a second
  real crawl.
- `--verify` — modifier flag, combine with any of the above. Runs **only**
  the offline verification pass: no network requests, no writes to
  `manifest.json` or `issues_registry.json`, no log file is even created.
  Checks per date: manifest↔registry consistency, raw page file existence +
  sha256, merged PDF existence + sha256 + page count, duplicate
  page_number/page_id; per year: cross-issue file contamination and
  duplicate merged-PDF content.

## Storage layout

```
data/raw/skrip/<year>/SKRIP_<year>-MM-DD/pages/       raw per-page PDFs
data/raw/skrip/<year>/SKRIP_<year>-MM-DD/manifest.json single source of truth for that issue's pages
data/raw/skrip/<year>/issues_registry.json            one entry per checked date, for that year
data/issues/skrip/<year>/SKRIP_<year>-MM-DD.pdf        merged issue PDF
logs/skrip/<year>.log                                  per-year event log
```

## Status meaning per date

- `complete` — every page downloaded, validated, and merged into a single PDF.
- `partial` — some pages downloaded, issue not yet mergeable; resumed on next run.
- `error` — a network/server-level failure (non-200 response, timeout,
  connection error). **Not** evidence the issue doesn't exist — no
  manifest is written, so the date is retried from scratch on the next run.
- `missing` — **"Το φύλλο δεν είναι διαθέσιμο στο ψηφιακό αρχείο του NLG για
  τη συγκεκριμένη ημερομηνία."** ("The issue is not available in the NLG
  digital archive for that date.") `pdfwin.asp`/`pages.asp` both returned
  HTTP 200 and the site's own markup explicitly says so
  (`<div class="infolabel">Το φύλλο δεν είναι διαθέσιμο.</div>`, with
  `nav_doc`/`pdf_doc` redirected to `navblank.htm`/`blankpdf.htm`), verified
  live against the site (see `--verify` and the 1923 diagnostic below).
  **This reflects digital-archive availability under this newspaper ID
  only.** It must not be read as historical proof the newspaper did not
  circulate on that date — only that no digitized/catalogued issue is
  available for it here.

## 1923-01-01 .. 1923-10-31: verified final state

Live-crawled and offline-verified: **20 complete, 284 missing, 0 partial, 0
error** — the registry covers the full range with no gaps. A targeted live
diagnostic against `pdfwin.asp`/`pages.asp` for five "missing" dates spread
across the range (1923-01-22, 02-01, 04-01, 06-15, 10-01), compared against
the last known-good date (1923-01-21), confirmed identical site markup,
identical session behavior, and the same explicit "not available" signal
every time — ruling out a parser/session regression as the cause of the 284
missing dates.
