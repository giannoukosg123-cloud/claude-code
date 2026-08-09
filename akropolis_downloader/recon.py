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

This script filters exclusively on Content-Type: application/pdf (never
images/CSS/JS) and tries two navigation strategies per page, to determine
which one reliably triggers that PDF request:

  A) Direct URL navigation: set the middle (main.asp) frame's own URL to
     main.asp?current={N+1} and see if that alone fires the PDF request --
     no button click at all.
  B) Fallback: click a real "next page" control inside that frame. The
     exact selector is NOT confirmed against live markup, so this also
     dumps every element with an onclick mentioning "current" as a
     diagnostic in case the guessed selectors below don't match.

A live run of Method A surfaced a real trap: navigating straight to a PDF
URL hands rendering to Chromium's OWN built-in PDF viewer (a component
extension, chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/...), which
then issues its own internal requests for its UI resources. Those got
mistaken for the real page content (536-byte "PDFs" that were actually
viewer-internal placeholders). Rather than depend on fragile/version-
specific Chromium flags to disable that built-in viewer, the capture logic
itself is now made immune to it: any response is rejected outright (never
silently accepted) if its URL starts with "chrome-extension://", or if its
body is smaller than MIN_VALID_PDF_BYTES -- real scanned pages are ~1-2MB,
so anything under that bar is refused as a placeholder and the wait
continues for a real candidate. See wait_for_real_pdf() -- this filtering
logic is confirmed correct and untouched by the change below.

A manual test then found the REAL navigation control: the frameset is
rows="45,81%,43" (top=header.asp, middle=main.asp, bottom=footer.asp), and
the "Μετάβαση σε σελίδα" page-number dropdown lives in the TOP (header.asp)
frame, not anywhere inside the main.asp content frame. Manually changing
that dropdown's value (12 -> 13) genuinely navigated to different content
(verified visually). So the real mechanism (Method C, now primary) is:
find the header.asp frame, find its page-number <select>, and
select_option() a target page number there -- letting the site's own
onchange handler do the real navigation (almost certainly straight to that
option's own main.asp?current=... value, which is why arithmetic
current-id guessing was always the wrong approach). The old Method A
(direct main_frame.goto) and Method B (guessing a "next" click target
inside main.asp) are kept only as a fallback if no header.asp frame or no
plausible page-number <select> is found.

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
import time
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
# Real scanned pages are ~1-2MB. Anything under this is refused outright as
# a placeholder/viewer-internal artifact -- never silently accepted.
MIN_VALID_PDF_BYTES = 50_000
SMALL_FILE_WARNING_BYTES = 100_000  # secondary post-hoc sanity check, above MIN_VALID_PDF_BYTES

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


def wait_for_real_pdf(page, trigger, timeout_ms: int, label: str):
    """Call trigger() (a navigation or click expected to fetch the real page
    PDF), then watch every response until one qualifies as REAL page
    content: not chrome-extension:// (Chromium's built-in PDF viewer's own
    internal resources), genuinely Content-Type: application/pdf, and at
    least MIN_VALID_PDF_BYTES large. Anything else is logged as a rejected
    candidate, never silently accepted, and the wait continues. Returns
    (response, body) or (None, None) on timeout."""
    state: dict = {}
    rejected: list[str] = []

    def handler(response) -> None:
        if "body" in state:
            return
        if response.url.startswith("chrome-extension://"):
            rejected.append(f"{response.url} (chrome-extension:// -- internal PDF viewer resource, not real content)")
            return
        try:
            content_type = response.headers.get("content-type", "")
        except Exception:  # noqa: BLE001
            return
        if content_type.split(";")[0].strip().lower() != "application/pdf":
            return
        try:
            body = response.body()
        except Exception as exc:  # noqa: BLE001
            rejected.append(f"{response.url} (could not read body: {exc})")
            return
        if len(body) < MIN_VALID_PDF_BYTES:
            rejected.append(f"{response.url} ({len(body)} bytes -- below {MIN_VALID_PDF_BYTES}, treated as placeholder)")
            return
        state["response"] = response
        state["body"] = body

    page.on("response", handler)
    try:
        trigger()
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline and "body" not in state:
            page.wait_for_timeout(200)
    except Exception as exc:  # noqa: BLE001
        print(f"    [{label}] trigger action itself failed: {exc}")
    finally:
        page.remove_listener("response", handler)

    for r in rejected:
        print(f"    [{label}] rejected candidate: {r}")

    if "body" in state:
        return state["response"], state["body"]
    return None, None


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


def find_header_frame(page):
    return next((f for f in page.frames if "header.asp" in f.url), None)


def find_page_number_select(frame):
    """Return (locator, options) for the <select> most likely to be the
    real 'Μετάβαση σε σελίδα' page-number dropdown confirmed to live in the
    header.asp frame: the one with the most purely-numeric option texts.
    options is a list of {"value", "text", "selected"} dicts. Returns
    (None, []) if nothing plausible is found."""
    try:
        selects = frame.locator("select").all()
    except Exception as exc:  # noqa: BLE001
        print(f"    could not query <select> in header frame: {exc}")
        return None, []

    best = None
    best_options: list[dict] = []
    best_numeric_count = 0
    for idx, sel_el in enumerate(selects):
        try:
            opts = sel_el.locator("option").all()
        except Exception:  # noqa: BLE001
            continue
        options = []
        numeric_count = 0
        for opt in opts:
            value = opt.get_attribute("value")
            text = opt.inner_text().strip()
            selected = opt.get_attribute("selected") is not None
            options.append({"value": value, "text": text, "selected": selected})
            if text.isdigit():
                numeric_count += 1
        print(f"    header <select> #{idx}: {len(options)} options, {numeric_count} purely numeric")
        if numeric_count > best_numeric_count:
            best_numeric_count = numeric_count
            best = sel_el
            best_options = options

    if best is not None and best_numeric_count >= 2:
        return best, best_options
    return None, []


def determine_start_page(options: list[dict]) -> int:
    for o in options:
        if o["selected"] and o["text"].isdigit():
            return int(o["text"])
    if options and options[0]["text"].isdigit():
        return int(options[0]["text"])
    return 1


def lookup_current_id_for_page(options: list[dict], target_page: int) -> "int | None":
    target_text = str(target_page)
    match = next((o for o in options if o["text"] == target_text), None)
    if match and match["value"]:
        m = CURRENT_RE.search(match["value"])
        if m:
            return int(m.group(1))
    return None


def select_page(select_locator, options: list[dict], target_page: int) -> None:
    """Trigger function for wait_for_real_pdf: select_option() the option
    whose visible text is exactly target_page's number. Its value almost
    certainly already IS main.asp?current=... -- we don't need to know
    or guess the current_id ourselves, the site's own onchange handler
    does the real navigation."""
    target_text = str(target_page)
    match = next((o for o in options if o["text"] == target_text), None)
    if match is None:
        raise RuntimeError(f"page {target_page} not present among header <select> options")
    if match["value"]:
        try:
            select_locator.select_option(value=match["value"])
            return
        except Exception:  # noqa: BLE001
            pass
    select_locator.select_option(label=target_text)


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

        header_frame = find_header_frame(viewer_page)
        page_select, select_options, start_page = None, [], None
        if header_frame:
            print(f"[5b] header.asp frame found: {header_frame.url}")
            page_select, select_options = find_page_number_select(header_frame)
            if page_select is not None:
                preview = (
                    select_options[:10] + [{"...": f"{len(select_options) - 15} more"}] + select_options[-5:]
                    if len(select_options) > 15 else select_options
                )
                print(f"    Confirmed page-number <select> with {len(select_options)} options. "
                      f"Preview: {preview}")
                start_page = determine_start_page(select_options)
                print(f"    Determined starting page number: {start_page}")
            else:
                print("    No <select> in the header frame looked like a page-number dropdown "
                      "(needed >=2 purely-numeric options). Falling back to Method A/B.")
        else:
            print("[5b] WARNING: no frame with 'header.asp' in its URL was found -- cannot use the "
                  "confirmed dropdown-based navigation. Falling back to Method A/B (unconfirmed).")

        for i in range(N_TEST_PAGES):
            pdf_response = None
            body = None
            current_id_for_manifest = None

            if page_select is not None and start_page is not None:
                # --- Method C (confirmed, primary): select_option() on the
                # header.asp frame's page-number dropdown. ---
                target_page = start_page + i
                current_id_for_manifest = lookup_current_id_for_page(select_options, target_page)
                print(f"\n[page {i + 1}/{N_TEST_PAGES}] target page number={target_page} "
                      f"(via header <select>; current_id from option value: {current_id_for_manifest})")

                pdf_response, body = wait_for_real_pdf(
                    viewer_page,
                    lambda tp=target_page: select_page(page_select, select_options, tp),
                    PDF_WAIT_TIMEOUT_MS,
                    "Method C",
                )
                if pdf_response is not None:
                    if method_used is None:
                        method_used = "C (select_option on header.asp's page-number dropdown)"
                    print(f"    Method C worked -- real application/pdf response: {pdf_response.url} ({len(body)} bytes)")
                else:
                    print(f"    Method C: no valid application/pdf response within timeout for page {target_page}.")
                    print("    Skipping this page -- see rejected-candidate lines above and recon_network_log.jsonl.")
                    continue
            else:
                # --- Fallback: old, unconfirmed Method A/B by current-id increment. ---
                target_current = FIRST_CURRENT_ID + i
                current_id_for_manifest = target_current
                new_url = CURRENT_RE.sub(f"current={target_current}", main_frame.url)
                print(f"\n[page {i + 1}/{N_TEST_PAGES}] target current={target_current} (fallback path)")

                print(f"    Method A: navigating main frame directly to {new_url}")
                pdf_response, body = wait_for_real_pdf(
                    viewer_page,
                    lambda u=new_url: main_frame.goto(u, wait_until="commit", timeout=PDF_WAIT_TIMEOUT_MS),
                    PDF_WAIT_TIMEOUT_MS,
                    "Method A",
                )
                if pdf_response is not None:
                    if method_used is None:
                        method_used = "A (direct frame URL navigation, no click)"
                    print(f"    Method A worked -- real application/pdf response: {pdf_response.url} ({len(body)} bytes)")
                else:
                    print("    Method A: no valid (non-placeholder, non-extension) application/pdf response within timeout.")

                if pdf_response is None:
                    print("    Trying Method B: clicking a 'next page' control ...")
                    dump_next_page_candidates(main_frame)

                    def click_next(i=i):
                        if not try_click_first(main_frame, NEXT_PAGE_SELECTORS, f"next-page-{i + 1}"):
                            raise RuntimeError("no guessed selector matched any element")

                    pdf_response, body = wait_for_real_pdf(viewer_page, click_next, PDF_WAIT_TIMEOUT_MS, "Method B")
                    if pdf_response is not None:
                        if method_used is None:
                            method_used = "B (clicking a next-page control)"
                        print(f"    Method B worked -- real application/pdf response: {pdf_response.url} ({len(body)} bytes)")
                    else:
                        print(f"    Method B also failed for current={target_current}.")
                        print("    Skipping this page -- see rejected-candidate lines above and recon_network_log.jsonl.")
                        continue

            captured_pages.append({
                "sequence_number": len(captured_pages) + 1,
                "current_id": current_id_for_manifest,
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
