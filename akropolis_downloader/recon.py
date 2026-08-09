"""AKROPOLIS microfilm reconnaissance -- Phase 1, continued (EXPLORATORY).

Independent tool. Nothing here is shared with skrip_downloader (different
site, different site logic: digitallib.parliament.gr vs efimeris.nlg.gr).

Confirmed so far from a local run (this sandbox's network egress policy
blocks digitallib.parliament.gr, so none of this has been reproduced here):

  - Viewer: display_doc.asp?item=40482&seg=... loads a frameset
    (top=header.asp, middle=main.asp, bottom=footer.asp).
  - Page navigation happens via the middle frame's URL: main.asp?current={N}
    (sequential integers; first page seen so far: current=7154207).
  - Images/MICROFILMS/low/{N}.jpg is a low-res thumbnail ONLY -- it doesn't
    even work as a direct HTTP request outside the browser session (returns
    the site's logo), and is NOT the real page content.
  - The real page content is a ~1-2MB application/pdf response with a
    random/UUID-style filename, fired with Initiator = main.asp?current=...
    -- NOT a predictable URL. It must be captured via network interception,
    once per page, keyed by *which current= value was active when it fired*
    (not by parsing the PDF response's own URL, which carries no page info).

This script now filters exclusively on Content-Type: application/pdf (never
images/CSS/JS) and tries two navigation strategies per page, to determine
which one reliably triggers that PDF request:

  A) Direct URL navigation: set the middle (main.asp) frame's own URL to
     main.asp?current={N+1} and see if that alone fires the PDF request --
     no button click at all.
  B) Fallback: click a real "next page" control inside that frame. The
     exact selector is NOT confirmed against live markup, so this also
     dumps every element with an onclick mentioning "current" as a
     diagnostic in case the guessed selectors below don't match.

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

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

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

# The first current= value seen so far. Override via env var if a fresh
# session starts elsewhere.
FIRST_CURRENT_ID = int(os.environ.get("AKROPOLIS_FIRST_CURRENT", "7154207"))
N_TEST_PAGES = 5
PDF_WAIT_TIMEOUT_MS = 20000
SMALL_FILE_WARNING_BYTES = 100_000  # a real scanned page should be well over this

CURRENT_RE = re.compile(r"current=(\d+)")

NEXT_PAGE_SELECTORS = [
    "a:has-text('▶')",
    "a:has-text('>')",
    "img[alt*='next' i]",
    "img[alt*='επόμεν' i]",
    "[title*='επόμεν' i]",
    "[title*='next' i]",
    "input[type=image]",
]


def is_pdf_response(response) -> bool:
    try:
        content_type = response.headers.get("content-type", "")
    except Exception:  # noqa: BLE001
        return False
    return content_type.split(";")[0].strip().lower() == "application/pdf"


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


def find_main_frame(page):
    return next((f for f in page.frames if "main.asp" in f.url), None)


def dump_forms_and_selects(page) -> None:
    """Diagnostic: log every <select> (name/id/options) and <form>
    (action/method) on the page, so a wrong/guessed selector or a value
    that needs explicit selecting is visible instead of silently assumed."""
    print("    <select> elements:")
    try:
        selects = page.locator("select").all()
    except Exception as exc:  # noqa: BLE001
        selects = []
        print(f"      could not query <select> elements: {exc}")
    if not selects:
        print("      (none found)")
    for sel_el in selects:
        name = sel_el.get_attribute("name")
        id_ = sel_el.get_attribute("id")
        options = []
        try:
            for opt in sel_el.locator("option").all():
                options.append({
                    "value": opt.get_attribute("value"),
                    "text": opt.inner_text().strip(),
                    "selected": opt.get_attribute("selected") is not None,
                })
        except Exception as exc:  # noqa: BLE001
            print(f"      could not read options: {exc}")
        print(f"      <select name={name!r} id={id_!r}> options={options}")

    print("    <form> elements:")
    try:
        forms = page.locator("form").all()
    except Exception as exc:  # noqa: BLE001
        forms = []
        print(f"      could not query <form> elements: {exc}")
    if not forms:
        print("      (none found)")
    for form_el in forms:
        print(f"      <form action={form_el.get_attribute('action')!r} "
              f"method={form_el.get_attribute('method')!r} "
              f"name={form_el.get_attribute('name')!r}>")


def try_explicit_select_for_item(page, item: int) -> bool:
    """If any <select> has an option whose value or text mentions our item
    number, select it explicitly -- a visually-preselected <option> does
    NOT guarantee the underlying JS/form state actually reflects it."""
    item_str = str(item)
    try:
        selects = page.locator("select").all()
    except Exception:  # noqa: BLE001
        return False
    for sel_el in selects:
        name = sel_el.get_attribute("name")
        id_ = sel_el.get_attribute("id")
        try:
            options = sel_el.locator("option").all()
        except Exception:  # noqa: BLE001
            continue
        for opt in options:
            value = opt.get_attribute("value") or ""
            text = opt.inner_text().strip()
            if item_str in value or item_str in text:
                target_sel = f"select[name='{name}']" if name else (f"#{id_}" if id_ else None)
                if not target_sel:
                    continue
                try:
                    page.select_option(target_sel, value=value if value else None, label=None if value else text)
                    print(f"    explicitly selected option (value={value!r}, text={text!r}) in {target_sel}")
                    return True
                except Exception as exc:  # noqa: BLE001
                    print(f"    select_option on {target_sel} failed: {exc}")
    return False


def dump_next_page_candidates(frame) -> None:
    """Diagnostic only: elements whose onclick mentions 'current' are the
    most likely real 'next page' control if the guessed selectors above
    don't match live markup."""
    print("    Elements with onclick mentioning 'current' (likely 'next page' candidates):")
    try:
        elements = frame.locator("[onclick]").all()
    except Exception as exc:  # noqa: BLE001
        print(f"    could not query onclick elements: {exc}")
        return
    found = 0
    for el in elements:
        try:
            onclick = el.get_attribute("onclick") or ""
        except Exception:  # noqa: BLE001
            continue
        if "current" in onclick.lower():
            found += 1
            try:
                tag = el.evaluate("e => e.tagName")
            except Exception:  # noqa: BLE001
                tag = "?"
            print(f"      <{tag} onclick=\"{onclick}\">")
    if not found:
        print("      (none found -- the real control may not use onclick at all, e.g. a plain <a href>)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_responses: list[dict] = []
    captured_pages: list[dict] = []  # {sequence_number, current_id, source_url, content_type, bytes_data}
    method_used = None

    netlog = open(NETWORK_LOG_PATH, "w", encoding="utf-8")

    def log_all(response) -> None:
        try:
            content_type = response.headers.get("content-type", "")
        except Exception:  # noqa: BLE001
            content_type = ""
        entry = {"url": response.url, "status": response.status, "content_type": content_type}
        all_responses.append(entry)
        netlog.write(json.dumps(entry, ensure_ascii=False) + "\n")
        netlog.flush()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, executable_path=CHROMIUM_EXECUTABLE_PATH)
        context = browser.new_context()
        page = context.new_page()
        page.on("response", log_all)

        print(f"[1] Opening {LIBRARY_URL}")
        page.goto(LIBRARY_URL, wait_until="networkidle", timeout=30000)
        (HERE / "recon_before_click.html").write_text(page.content(), encoding="utf-8")

        print("[2] Inspecting <select>/<form> elements before touching anything ...")
        dump_forms_and_selects(page)

        print(f"[2b] Explicitly selecting the option for item={ITEM}, if one exists "
              f"(a visually-preselected <option> doesn't guarantee JS/form state agrees) ...")
        explicitly_selected = try_explicit_select_for_item(page, ITEM)
        if not explicitly_selected:
            print("    No matching <option> found to select explicitly -- proceeding as-is.")

        print("[3] Clicking 'Μετάβαση' link/button, watching for a possible new tab/popup "
              "(known cause: this site opens the viewer via window.open/target=_blank) ...")
        MET_SELECTORS = [
            "text=Μετάβαση", "a:has-text('Μετάβαση')", "input[value*='Μετάβαση']", "button:has-text('Μετάβαση')",
        ]
        new_page = None
        clicked = False
        try:
            with context.expect_page(timeout=15000) as new_page_info:
                clicked = try_click_first(page, MET_SELECTORS, "Μετάβαση")
                if not clicked:
                    raise RuntimeError("no selector matched a clickable 'Μετάβαση' element")
            new_page = new_page_info.value
            new_page.on("response", log_all)
            new_page.wait_for_load_state("networkidle", timeout=45000)
            print(f"    A new tab/window opened, as suspected: {new_page.url}")
        except PlaywrightTimeoutError:
            print("    No new tab/window opened within 15s after the click -- "
                  "falling back to same-page navigation handling.")
        except RuntimeError as exc:
            print(f"    {exc} -- see recon_before_click.html for the real markup.")

        # Work on whichever page object actually holds the viewer: the new
        # tab if one opened, otherwise the original page (same-page fallback).
        viewer_page = new_page if new_page is not None else page

        # Dump state right away, before any further waiting, from whichever
        # page is now the viewer candidate.
        (HERE / "recon_after_click.html").write_text(viewer_page.content(), encoding="utf-8")
        print(f"    URL right after click handling: {viewer_page.url}")

        if new_page is None:
            # Same-page fallback path: give it the same settle/second-chance
            # treatment as before, in case this site sometimes does navigate
            # in-place instead of popping a new tab.
            print("[4] Waiting for navigation to settle (networkidle, up to 45s) ...")
            try:
                viewer_page.wait_for_load_state("networkidle", timeout=45000)
            except PlaywrightTimeoutError:
                print("    WARNING: networkidle wait timed out -- page may be doing long-polling/ajax "
                      "that never goes idle. Continuing to check the URL/frames anyway.")

            print(f"[4b] URL after waiting: {viewer_page.url}")
            (HERE / "recon_after_wait.html").write_text(viewer_page.content(), encoding="utf-8")

            if viewer_page.url == LIBRARY_URL or "display_doc.asp" not in viewer_page.url:
                print(f"    URL did not change to display_doc.asp (still {viewer_page.url}). "
                      f"Trying an explicit wait_for_url('display_doc') as a second chance (15s) ...")
                try:
                    viewer_page.wait_for_url(re.compile("display_doc", re.IGNORECASE), timeout=15000)
                    print(f"    wait_for_url succeeded -- URL now: {viewer_page.url}")
                except PlaywrightTimeoutError:
                    print(f"    wait_for_url also timed out -- URL is still: {viewer_page.url}")

        main_frame = find_main_frame(viewer_page)
        if not main_frame:
            print("\nFATAL: no frame with 'main.asp' in its URL was found "
                  f"({'in the new tab' if new_page is not None else 'on the original page'}). "
                  "Dumping page + frame HTML for manual inspection -- cannot proceed with PDF "
                  "capture without it. Check recon_before_click.html vs recon_after_click.html "
                  "(vs recon_after_wait.html, if no new tab opened) to see exactly what changed.")
            (HERE / "recon_viewer.html").write_text(viewer_page.content(), encoding="utf-8")
            for i, frame in enumerate(viewer_page.frames):
                try:
                    (HERE / f"recon_frame_{i}.html").write_text(frame.content(), encoding="utf-8")
                except Exception as exc:  # noqa: BLE001
                    print(f"    could not dump frame {i}: {exc}")
            netlog.close()
            browser.close()
            return

        print(f"[5] main.asp frame found "
              f"{'in the new tab/window' if new_page is not None else 'on the original page'}: {main_frame.url}")

        for i in range(N_TEST_PAGES):
            target_current = FIRST_CURRENT_ID + i
            new_url = CURRENT_RE.sub(f"current={target_current}", main_frame.url)
            print(f"\n[page {i + 1}/{N_TEST_PAGES}] target current={target_current}")

            pdf_response = None

            # --- Method A: direct frame URL navigation, no click at all. ---
            print(f"    Method A: navigating main frame directly to {new_url}")
            try:
                with viewer_page.expect_response(is_pdf_response, timeout=PDF_WAIT_TIMEOUT_MS) as resp_info:
                    main_frame.goto(new_url, wait_until="commit", timeout=PDF_WAIT_TIMEOUT_MS)
                pdf_response = resp_info.value
                if method_used is None:
                    method_used = "A (direct frame URL navigation, no click)"
                print(f"    Method A worked -- application/pdf response: {pdf_response.url}")
            except PlaywrightTimeoutError:
                print("    Method A: no application/pdf response within timeout.")
            except Exception as exc:  # noqa: BLE001
                print(f"    Method A: navigation itself failed: {exc}")

            # --- Method B: click a real 'next page' control. ---
            if pdf_response is None:
                print("    Trying Method B: clicking a 'next page' control ...")
                dump_next_page_candidates(main_frame)
                try:
                    with viewer_page.expect_response(is_pdf_response, timeout=PDF_WAIT_TIMEOUT_MS) as resp_info:
                        if not try_click_first(main_frame, NEXT_PAGE_SELECTORS, f"next-page-{i + 1}"):
                            raise RuntimeError("no guessed selector matched any element")
                    pdf_response = resp_info.value
                    if method_used is None:
                        method_used = "B (clicking a next-page control)"
                    print(f"    Method B worked -- application/pdf response: {pdf_response.url}")
                except Exception as exc:  # noqa: BLE001
                    print(f"    Method B also failed for current={target_current}: {exc}")
                    print("    Skipping this page -- see recon_network_log.jsonl and the onclick dump above.")
                    continue

            body = pdf_response.body()
            captured_pages.append({
                "sequence_number": len(captured_pages) + 1,
                "current_id": target_current,
                "source_url": pdf_response.url,
                "content_type": pdf_response.headers.get("content-type", ""),
                "bytes_data": body,
            })
            print(f"    captured {len(body)} bytes")

        netlog.close()
        browser.close()

    print(f"\n[Result] Logged {len(all_responses)} total network responses -> {NETWORK_LOG_PATH}")
    print(f"[Result] Navigation method that worked: {method_used or 'NEITHER -- see per-page errors above'}")
    print(f"[Result] Captured {len(captured_pages)}/{N_TEST_PAGES} real application/pdf pages.")

    if not captured_pages:
        print("\nNo PDF pages captured at all. Inspect recon_network_log.jsonl for what actually "
              "fired during navigation attempts, and the onclick dumps above for the real "
              "'next page' control if Method A didn't work.")
        return

    manifest_pages = []
    for p_ in captured_pages:
        filename = f"page_{p_['sequence_number']:04d}.pdf"
        dest = OUT_DIR / filename
        dest.write_bytes(p_["bytes_data"])
        digest = sha256_bytes(p_["bytes_data"])
        manifest_pages.append({
            "sequence_number": p_["sequence_number"],
            "current_id": p_["current_id"],
            "source_url": p_["source_url"],
            "content_type": p_["content_type"],
            "local_filename": filename,
            "bytes": len(p_["bytes_data"]),
            "sha256": digest,
        })
        print(f"    saved {dest} ({len(p_['bytes_data'])} bytes) current_id={p_['current_id']} sha256={digest[:16]}...")

    manifest = {
        "newspaper": "ΑΚΡΟΠΟΛΙΣ",
        "source": "digitallib.parliament.gr",
        "item": ITEM,
        "phase": "1-reconnaissance-test-pdf",
        "navigation_method": method_used,
        "pages": manifest_pages,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote manifest: {MANIFEST_PATH}")

    sizes = [p["bytes"] for p in manifest_pages]
    print(f"\nPage sizes (bytes): {sizes}")
    small = [p for p in manifest_pages if p["bytes"] < SMALL_FILE_WARNING_BYTES]
    if small:
        print(f"WARNING: {len(small)} captured page(s) are under {SMALL_FILE_WARNING_BYTES} bytes -- "
              f"these may be decorative/placeholder PDFs rather than real scanned pages. "
              f"Open them manually to check before trusting this pattern for the full 456-page crawl.")
    else:
        print("All captured pages are comfortably above the placeholder-size threshold -- "
              "consistent with real scanned page content.")

    current_ids = [p["current_id"] for p in manifest_pages]
    if len(current_ids) >= 2:
        deltas = [b - a for a, b in zip(current_ids, current_ids[1:])]
        print(f"\ncurrent= values used: {current_ids}")
        print(f"deltas: {deltas} "
              f"({'all +1, as expected' if all(d == 1 for d in deltas) else 'NOT all +1 -- unexpected'})")


if __name__ == "__main__":
    main()
