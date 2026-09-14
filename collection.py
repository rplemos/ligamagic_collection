import os
import re
import sys
import csv
import argparse
import urllib.parse as urlparse
from urllib.parse import parse_qs
from playwright.sync_api import sync_playwright

HEADER_ROW = ['Name', 'Edition', 'Price', 'Language', 'Condition', 'Extras']
BASE_URL = "https://www.ligamagic.com.br"
ROW_SELECTOR = 'tr.pointer[id^="cc_card_"]'

# A free-tier container gets a fraction of a CPU, so rendering that takes two
# seconds on a laptop can take far longer there. These are deliberately roomy.
NAV_TIMEOUT_MS = int(os.environ.get("NAV_TIMEOUT_MS", 90_000))
SELECTOR_TIMEOUT_MS = int(os.environ.get("SELECTOR_TIMEOUT_MS", 60_000))

# Headless Chromium's default User-Agent announces "HeadlessChrome", which some
# bot filters reject outright. Present as ordinary desktop Chrome instead.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")

# Chromium flags that matter inside a small container. The big one is
# --disable-dev-shm-usage: a container's /dev/shm defaults to 64 MB, which
# Chromium will exhaust and then crash. --no-sandbox is needed because the
# container isn't privileged.
BROWSER_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-extensions",
]

# Images, fonts and video are pure overhead here. Note the Language column is
# read from an <img> tag's src attribute, which is present in the DOM whether
# or not the image itself is ever downloaded.
#
# Set BLOCK_RESOURCES=0 to turn this off without a code change — useful for
# ruling it out if a page stops rendering on a host.
BLOCKED_RESOURCES = {"image", "font", "media"}
BLOCK_RESOURCES = os.environ.get("BLOCK_RESOURCES", "1") != "0"


def launch_browser(playwright):
    """Starts Chromium with flags suited to a memory-constrained host."""
    return playwright.chromium.launch(headless=True, args=BROWSER_ARGS)


def new_page(browser):
    """Opens a page that looks like ordinary desktop Chrome."""
    context = browser.new_context(
        user_agent=USER_AGENT,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        viewport={"width": 1280, "height": 900},
        extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
    )
    context.set_default_timeout(SELECTOR_TIMEOUT_MS)
    context.set_default_navigation_timeout(NAV_TIMEOUT_MS)

    page = context.new_page()

    if BLOCK_RESOURCES:
        def filter_request(route):
            if route.request.resource_type in BLOCKED_RESOURCES:
                route.abort()
            else:
                route.continue_()

        page.route("**/*", filter_request)

    return page


def describe_failure(page, error):
    """Explains why no card rows showed up, based on what's actually on screen.

    A bare "timeout" is useless when the same code works locally — the useful
    question is whether we got the real page, a bot challenge, or nothing.
    """
    kind = type(error).__name__
    try:
        html = page.content()
        title = (page.title() or "").strip()
        url = page.url
    except Exception:
        return f"{kind}, and the page itself could not be read"

    # Search the title too — the challenge page is localised, so the giveaway
    # may only be in the title ("Um momento…" is the pt-BR "Just a moment…").
    haystack = (html + " " + title).lower()

    # Any of these means the request was refused before the real page loaded.
    BLOCK_MARKERS = [
        ("just a moment", "Cloudflare interstitial"),
        ("um momento", "Cloudflare interstitial (pt-BR)"),
        ("challenge-platform", "Cloudflare challenge script"),
        ("cdn-cgi/challenge", "Cloudflare challenge"),
        ("cf-browser-verification", "Cloudflare verification"),
        ("cf-challenge", "Cloudflare challenge"),
        ("checking your browser", "browser check"),
        ("verificando seu navegador", "browser check (pt-BR)"),
        ("captcha", "captcha"),
        ("attention required", "Cloudflare block page"),
        ("acesso negado", "'acesso negado'"),
        ("access denied", "'access denied'"),
        ("too many requests", "rate limited"),
    ]
    OTHER_MARKERS = [
        ("cc_card_", "card rows ARE in the HTML"),
        ("card=", "card links present"),
    ]

    blocked = [label for needle, label in BLOCK_MARKERS if needle in haystack]
    others = [label for needle, label in OTHER_MARKERS if needle in haystack]
    signals = blocked + others

    detail = (f"url={url} title={title!r} html={len(html):,} chars; "
              f"signals: {', '.join(signals) if signals else 'none'}")

    if blocked:
        return (f"BLOCKED — this host's IP is being refused by the site's bot "
                f"protection, not a timeout. {detail}")

    return f"{kind}: no card rows appeared. {detail}"


def log(message):
    """Prints status updates to stderr so they don't corrupt the stdout TSV data."""
    print(message, file=sys.stderr)


def parse_total(page):
    """Reads the collection grand total from the '<b>144x</b>' footer cell."""
    cells = page.locator('td.col-total')
    for i in range(cells.count()):
        text = cells.nth(i).inner_text().strip()
        match = re.search(r'(\d+)\s*x', text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def extract_row(row):
    """Pulls the card data out of a single collection table row."""
    name = 'Not found'
    edition = 'Not found'
    price = 'Not found'
    language = '-'
    condition = '-'
    extras = '-'

    # 1. Name and Edition
    td_card_info = row.locator('td').nth(3)
    if td_card_info.locator('a').count() > 0:
        href = td_card_info.locator('a').first.get_attribute('href')
        if href:
            query_params = parse_qs(urlparse.urlparse(href).query)
            if 'card' in query_params:
                name = query_params['card'][0].replace('+', ' ').replace('"', '').strip()
            if 'ed' in query_params:
                edition = query_params['ed'][0].upper()

    # 2. Extras
    td_extras = row.locator('td').nth(4)
    if td_extras.count() > 0:
        extracted_extras = td_extras.inner_text().strip()
        if extracted_extras:
            extras = extracted_extras

    # 3. Language
    td_lang = row.locator('td').nth(5)
    if td_lang.locator('img').count() > 0:
        src = td_lang.locator('img').first.get_attribute('src')
        if src:
            language = src.split('/')[-1].split('.')[0].upper()

    # 4. Condition
    td_cond = row.locator('td').nth(6)
    if td_cond.count() > 0:
        extracted_cond = td_cond.inner_text().strip()
        if extracted_cond:
            condition = extracted_cond

    # 5. Price
    td_price = row.locator('td.col-pcompra')
    if td_price.count() > 0:
        raw_price = td_price.inner_text().strip()
        if 'R$' in raw_price:
            price = raw_price.replace('R$', '').strip().replace('.', '').replace(',', '.')
        else:
            price = raw_price

    return [name, edition, price, language, condition, extras]


def scrape_collection(page, collection_id, on_progress=None):
    """Scrapes every page of a collection.

    Returns (rows, total, unique) where 'total' is the collection grand total
    (counting duplicate copies) and 'unique' is the number of table rows.

    'on_progress' is an optional callable receiving status strings; it defaults
    to writing them to stderr.
    """
    report = on_progress or log

    current_url = f"{BASE_URL}/?view=colecao/colecao&id={collection_id}"
    rows_data = []
    total = None
    page_num = 1

    while current_url:
        try:
            # "domcontentloaded" rather than "networkidle": networkidle waits for
            # the network to go quiet, which analytics beacons and aborted
            # requests can prevent indefinitely. The selector wait below is the
            # real signal that the table has rendered.
            page.goto(current_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            page.wait_for_selector(ROW_SELECTOR, timeout=SELECTOR_TIMEOUT_MS)
        except Exception as error:
            report(f"Collection {collection_id}, page {page_num}: {describe_failure(page, error)}")
            break

        if total is None:
            total = parse_total(page)

        rows = page.locator(ROW_SELECTOR)
        for i in range(rows.count()):
            rows_data.append(extract_row(rows.nth(i)))

        report(f"Collection {collection_id}: page {page_num} done ({len(rows_data)} rows so far).")

        # Next page
        current_url = None
        next_btn = page.locator('div.direita-paginacao a:text-is(">")')
        if next_btn.count() > 0:
            next_href = next_btn.first.get_attribute('href')
            if next_href and next_href != "#":
                current_url = urlparse.urljoin(BASE_URL, next_href)
                page_num += 1

    unique = len(rows_data)
    if total is None:
        total = unique

    return rows_data, total, unique


def compare_collections(id_reference, id_target):
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = new_page(browser)

        log(f"Scraping reference collection {id_reference}...")
        ref_rows, ref_total, ref_unique = scrape_collection(page, id_reference)

        log(f"Scraping target collection {id_target}...")
        tgt_rows, tgt_total, tgt_unique = scrape_collection(page, id_target)

        browser.close()

    ref_names = {row[0].strip().lower() for row in ref_rows}

    writer = csv.writer(sys.stdout, delimiter='\t', lineterminator='\n')
    writer.writerow(HEADER_ROW)
    for row in tgt_rows:
        if row[0].strip().lower() in ref_names:
            writer.writerow(row)

    print()
    print(f"Collection 1: {ref_total} ({ref_unique} unique)")
    print(f"Collection 2: {tgt_total} ({tgt_unique} unique)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compare two LigaMagic collections: prints the listings from the "
                    "second collection whose cards also appear in the first."
    )
    parser.add_argument("collection_1", help="ID of the reference collection (the one to compare to).")
    parser.add_argument("collection_2", help="ID of the collection to be compared.")

    args = parser.parse_args()

    compare_collections(args.collection_1, args.collection_2)
