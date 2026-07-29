"""Checks whether a LigaMagic collection page can be read without a browser.

If the card rows are present in the raw HTML, we can drop Playwright and
Chromium entirely — which makes the app small enough to host free almost
anywhere. If they're not, the page is built by JavaScript and Playwright stays.

Usage:
    python3 probe.py 435257

Uses only the standard library, so there's nothing to install.
"""

import gzip
import re
import sys
import zlib
import urllib.request
import urllib.error

BASE = "https://www.ligamagic.com.br/?view=colecao/colecao&id="

# Look like a normal browser; a bare urllib user-agent is often refused.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Connection": "close",
}


def decode(response):
    raw = response.read()
    encoding = (response.headers.get("Content-Encoding") or "").lower()
    if "gzip" in encoding:
        raw = gzip.decompress(raw)
    elif "deflate" in encoding:
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Usage: python3 probe.py <collection_id>")
        return 2

    url = BASE + sys.argv[1]
    print(f"Fetching {url}\n")

    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            html = decode(response)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {e.reason}")
        print("\nVERDICT: blocked or unavailable without a browser. Keep Playwright.")
        return 1
    except Exception as e:
        print(f"Request failed: {e}")
        print("\nVERDICT: could not fetch. Keep Playwright.")
        return 1

    card_rows = re.findall(r'id="cc_card_\d+"', html)
    total_cell = re.search(r'class="col-total"[^>]*>\s*<b>\s*(\d+)\s*x', html)
    names = re.findall(r'[?&]card=([^&"\']+)', html)

    print(f"HTTP status:         {status}")
    print(f"HTML size:           {len(html):,} chars")
    print(f'id="cc_card_N" rows: {len(card_rows)}')
    print(f"col-total found:     {total_cell.group(1) if total_cell else 'no'}")
    print(f"card= params:        {len(names)}")
    if names:
        sample = [n.replace('+', ' ')[:30] for n in names[:5]]
        print(f"Sample card params:  {sample}")

    # The card names turning up without matching rows means the data is in the
    # HTML under different markup than expected. Dump the file and show some
    # context so the real structure can be read off it.
    dump_path = "probe_dump.html"
    with open(dump_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nFull HTML saved to:  {dump_path}")

    print("\n--- other id= patterns on the page (top 15 by count) ---")
    ids = re.findall(r'id="([a-zA-Z_][a-zA-Z0-9_]*?)_?\d*"', html)
    counts = {}
    for name in ids:
        counts[name] = counts.get(name, 0) + 1
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {count:5d}  id=\"{name}...\"")

    print("\n--- 'total' / 'col-' class names present ---")
    classes = sorted(set(re.findall(r'class="((?:col-|[a-z-]*total)[a-zA-Z0-9_ -]*)"', html)))
    for name in classes[:20]:
        print(f"  class=\"{name}\"")
    if not classes:
        print("  (none)")

    print("\n--- context around the first card= link ---")
    first = re.search(r'[?&]card=', html)
    if first:
        start = max(0, first.start() - 700)
        print(html[start:first.start() + 700])

    print()
    if card_rows and total_cell:
        print("VERDICT: the page is server-rendered. Playwright can be dropped —")
        print("         a plain HTTP fetch plus an HTML parser is enough.")
        return 0
    if names:
        print("VERDICT: mixed. The card data IS in the raw HTML, so the server")
        print("         does render it — but not in the markup collection.py")
        print(f"         expects. Send me {dump_path} and the sections above.")
        return 0
    print("VERDICT: no card data in the raw HTML, so the table is rendered by")
    print("         JavaScript (or the request was soft-blocked). Keep Playwright.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
