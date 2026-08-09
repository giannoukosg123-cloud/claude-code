"""AKROPOLIS microfilm reconnaissance -- Phase 1 (EXPLORATORY, not validated).

Independent tool. Nothing here is shared with skrip_downloader (different
site, different site logic: digitallib.parliament.gr vs efimeris.nlg.gr).

Investigates how digitallib.parliament.gr serves individual microfilm page
images/PDFs for item=40482 (ΑΚΡΟΠΟΛΙΣ, Jul-Sep 1935, 456 pages total). The
selectors below ("Μετάβαση" link, "next page" control) are best-effort
guesses based on the task description, NOT confirmed against live markup --
this environment has no network access to digitallib.parliament.gr to
verify them. Run this locally with HEADLESS=0 the first time so you can see
exactly what happens and fix any selector that doesn't match, using the
dumped recon_viewer.html / recon_frame_*.html / recon_network_log.jsonl as
evidence.

Usage:
    pip install playwright
    playwright install chromium
    HEADLESS=0 python3 recon.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ITEM = 40482
LIBRARY_URL = f"http://digitallib.parliament.gr/library.asp?item={ITEM}"

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
OUT_DIR = REPO_ROOT / "data" / "raw" / "akropolis" / "1935-07-09" / "pages"
MANIFEST_PATH = OUT_DIR.parent / "manifest.json"
NETWORK_LOG_PATH = HERE / "recon_network_log.jsonl"

HEADLESS = os.environ.get("HEADLESS", "1") != "0"
# Only needed if your local `playwright install`ed Chromium build doesn't
# match the installed `playwright` pip package's expected version (Playwright
# will tell you loudly if so). Leave unset normally.
CHROMIUM_EXECUTABLE_PATH = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or None
N_TEST_PAGES = 5

CURRENT_RE = re.compile(r"current=(\d+)")

CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
}


def is_page_image_response(content_type: str, url: str) -> bool:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in CONTENT_TYPE_EXT:
        return True
    return bool(re.search(r"\.(jpe?g|png|tiff?|gif|pdf)(\?|$)", url, re.IGNORECASE))


def ext_for(content_type: str, url: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in CONTENT_TYPE_EXT:
        return CONTENT_TYPE_EXT[ct]
    m = re.search(r"\.(jpe?g|png|tiff?|gif|pdf)(\?|$)", url, re.IGNORECASE)
    return f".{m.group(1).lower()}" if m else ".bin"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def try_click_first(target, selectors: list[str], label: str) -> bool:
    for sel in selectors:
        try:
            loc = target.locator(sel)
            if loc.count() > 0:
                loc.first.click()
                print(f"    [{label}] clicked selector: {sel}")
                return True
        except Exception as exc:  # noqa: BLE001
            print(f"    [{label}] selector {sel!r} failed: {exc}")
    return False


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_responses: list[dict] = []
    candidate_pages: list[dict] = []  # in first-seen order: {url, content_type, bytes}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, executable_path=CHROMIUM_EXECUTABLE_PATH)
        context = browser.new_context()
        page = context.new_page()

        netlog = open(NETWORK_LOG_PATH, "w", encoding="utf-8")

        def on_response(response) -> None:
            try:
                headers = response.headers
                content_type = headers.get("content-type", "")
            except Exception:  # noqa: BLE001
                content_type = ""

            entry = {
                "url": response.url,
                "status": response.status,
                "content_type": content_type,
                "method": response.request.method,
            }
            all_responses.append(entry)
            netlog.write(json.dumps(entry, ensure_ascii=False) + "\n")
            netlog.flush()

            if is_page_image_response(content_type, response.url) and len(candidate_pages) < N_TEST_PAGES:
                try:
                    body = response.body()
                except Exception as exc:  # noqa: BLE001
                    print(f"    could not read body of {response.url}: {exc}")
                    return
                already = any(c["url"] == response.url for c in candidate_pages)
                if not already:
                    candidate_pages.append({
                        "url": response.url,
                        "content_type": content_type,
                        "bytes": body,
                    })
                    print(f"    CAPTURED candidate page image #{len(candidate_pages)}: "
                          f"{response.url} ({content_type}, {len(body)} bytes)")

        page.on("response", on_response)

        print(f"[1] Opening {LIBRARY_URL}")
        page.goto(LIBRARY_URL, wait_until="networkidle", timeout=30000)

        print("[2] Looking for a 'Μετάβαση' link/button to enter the viewer ...")
        clicked = try_click_first(
            page,
            [
                "text=Μετάβαση",
                "a:has-text('Μετάβαση')",
                "input[value*='Μετάβαση']",
                "button:has-text('Μετάβαση')",
            ],
            "Μετάβαση",
        )
        if not clicked:
            print("    WARNING: automatic click failed. Open recon_viewer.html (dumped below) "
                  "to find the real link/onclick and update this script's selectors.")

        page.wait_for_load_state("networkidle", timeout=30000)
        print(f"[3] URL after navigation: {page.url}")

        (HERE / "recon_viewer.html").write_text(page.content(), encoding="utf-8")
        for i, frame in enumerate(page.frames):
            print(f"    frame[{i}] url={frame.url}")
            try:
                (HERE / f"recon_frame_{i}.html").write_text(frame.content(), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                print(f"    could not dump frame {i}: {exc}")

        main_frame = next((f for f in page.frames if "main.asp" in f.url), None)
        if main_frame:
            print(f"[4] Found a main.asp frame: {main_frame.url}")
            m = CURRENT_RE.search(main_frame.url)
            if m:
                print(f"    First page current= value: {m.group(1)}")
        else:
            print("[4] WARNING: no frame with 'main.asp' in its URL found. "
                  "Check recon_viewer.html / recon_frame_*.html manually -- "
                  "the viewer may use a different frame name or load main.asp via JS/XHR "
                  "instead of a plain <iframe src=...>.")

        target = main_frame or page
        print("[4b] Inline <img>/<embed>/<object> elements in the viewer frame:")
        for tag, attr in [("img", "src"), ("embed", "src"), ("object", "data")]:
            try:
                for el in target.locator(tag).all():
                    src = el.get_attribute(attr)
                    if src:
                        print(f"    <{tag} {attr}='{src}'>")
            except Exception:  # noqa: BLE001
                pass

        print(f"\n[5] Clicking 'next page' up to {N_TEST_PAGES - 1} more times "
              f"(need {N_TEST_PAGES} pages total for the Phase 1 test download) ...")
        for i in range(N_TEST_PAGES - 1):
            clicked_next = try_click_first(
                target,
                [
                    "text=Επόμενη",
                    "a:has-text('Επόμενη')",
                    "img[alt*='next' i]",
                    "img[alt*='επόμεν' i]",
                    "[title*='επόμεν' i]",
                ],
                f"next-page-{i + 1}",
            )
            if not clicked_next:
                print(f"    step {i + 1}: could not find a 'next page' control automatically. "
                      f"Inspect recon_frame_*.html for the real element/onclick "
                      f"(likely something calling main.asp?current={{N+1}} directly) and fix this script.")
                break
            page.wait_for_timeout(1500)
            current_frame = next((f for f in page.frames if "main.asp" in f.url), main_frame)
            print(f"    step {i + 1}: viewer frame url now = {current_frame.url if current_frame else page.url}")

        netlog.close()
        browser.close()

    print(f"\n[6] Logged {len(all_responses)} network responses -> {NETWORK_LOG_PATH}")
    print(f"[7] Captured {len(candidate_pages)}/{N_TEST_PAGES} candidate page images/PDFs.")

    if candidate_pages:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        manifest_pages = []
        for i, cand in enumerate(candidate_pages, start=1):
            ext = ext_for(cand["content_type"], cand["url"])
            filename = f"page_{i:04d}{ext}"
            dest = OUT_DIR / filename
            dest.write_bytes(cand["bytes"])
            m = CURRENT_RE.search(cand["url"])
            current_id = m.group(1) if m else None
            manifest_pages.append({
                "sequence_number": i,
                "current_id": current_id,
                "source_url": cand["url"],
                "content_type": cand["content_type"],
                "local_filename": filename,
                "bytes": len(cand["bytes"]),
                "sha256": sha256_bytes(cand["bytes"]),
            })
            print(f"    saved {dest} ({len(cand['bytes'])} bytes, current_id={current_id})")

        manifest = {
            "newspaper": "ΑΚΡΟΠΟΛΙΣ",
            "source": "digitallib.parliament.gr",
            "item": ITEM,
            "phase": "1-reconnaissance-test",
            "pages": manifest_pages,
        }
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote manifest: {MANIFEST_PATH}")

        current_ids = [p["current_id"] for p in manifest_pages if p["current_id"] is not None]
        if len(current_ids) >= 2:
            ints = [int(c) for c in current_ids]
            deltas = [b - a for a, b in zip(ints, ints[1:])]
            print(f"\ncurrent= values observed: {ints}")
            print(f"deltas between consecutive pages: {deltas} "
                  f"({'all +1 -- consistent with 456 sequential pages' if all(d == 1 for d in deltas) else 'NOT all +1 -- investigate before assuming linear coverage'})")
    else:
        print("\nNo candidate page images/PDFs were auto-captured. Inspect "
              f"{NETWORK_LOG_PATH} by hand for the response whose content-type is "
              "image/* or application/pdf, and/or open recon_frame_*.html to find "
              "the real <img>/<embed> src -- then adjust is_page_image_response()/"
              "the click selectors above accordingly.")


if __name__ == "__main__":
    main()
