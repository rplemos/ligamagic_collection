"""Web front-end for collection.py.

Two collection IDs in, the matching listings out, with a live progress log and
a TSV download. Run with:

    python3 app.py

then open http://localhost:5000
"""

import io
import re
import csv
import json
import os
import queue
import threading
import traceback
import urllib.parse as urlparse

from flask import Flask, Response, render_template, request
from playwright.sync_api import sync_playwright

from collection import HEADER_ROW, launch_browser, new_page, scrape_collection

app = Flask(__name__)

# Sent every few seconds while a long page load is in flight so proxies and
# browsers don't drop an apparently idle connection.
HEARTBEAT_SECONDS = 15


def clean_id(raw):
    """Accepts a bare ID or a full LigaMagic collection URL, returns the ID."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "id=" in raw:
        params = urlparse.parse_qs(urlparse.urlparse(raw).query)
        if "id" in params:
            raw = params["id"][0]
    return raw if re.fullmatch(r"\d+", raw) else None


def sse(event, payload):
    """Formats a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def run_comparison(id_1, id_2, events):
    """Scrapes both collections, pushing (event, payload) tuples onto a queue.

    Runs on a worker thread so the response generator can forward progress to
    the browser while the scrape is still going.
    """
    try:
        with sync_playwright() as p:
            browser = launch_browser(p)
            page = new_page(browser)
            try:
                def report(message):
                    events.put(("log", message))

                report(f"Scraping collection 1 ({id_1})...")
                ref_rows, ref_total, ref_unique = scrape_collection(page, id_1, on_progress=report)
                report(f"Collection 1: {ref_total} cards ({ref_unique} unique).")

                report(f"Scraping collection 2 ({id_2})...")
                tgt_rows, tgt_total, tgt_unique = scrape_collection(page, id_2, on_progress=report)
                report(f"Collection 2: {tgt_total} cards ({tgt_unique} unique).")
            finally:
                browser.close()

        ref_names = {row[0].strip().lower() for row in ref_rows}
        matches = [row for row in tgt_rows if row[0].strip().lower() in ref_names]

        events.put(("log", f"Comparing... {len(matches)} matching listings found."))

        for row in matches:
            events.put(("row", row))

        events.put(("done", {
            "matches": len(matches),
            "c1": {"id": id_1, "total": ref_total, "unique": ref_unique},
            "c2": {"id": id_2, "total": tgt_total, "unique": tgt_unique},
        }))
    except Exception:
        traceback.print_exc()
        events.put(("failed", "Scraping failed. See the terminal running this app for details."))
    finally:
        events.put(None)


@app.route("/")
def index():
    return render_template("index.html", header=HEADER_ROW)


@app.route("/stream")
def stream():
    id_1 = clean_id(request.args.get("c1"))
    id_2 = clean_id(request.args.get("c2"))

    def generate():
        if not id_1 or not id_2:
            yield sse("failed","Both collection IDs are required and must be numeric.")
            return

        events = queue.Queue()
        worker = threading.Thread(target=run_comparison, args=(id_1, id_2, events), daemon=True)
        worker.start()

        while True:
            try:
                item = events.get(timeout=HEARTBEAT_SECONDS)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if item is None:
                break
            yield sse(*item)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/tsv", methods=["POST"])
def tsv():
    """Builds the TSV from the rows the browser currently has on screen.

    The browser sends whatever survived its filters, so the download always
    matches the visible table.
    """
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows", [])
    collection = clean_id(payload.get("collection"))

    filename = f"collection_{collection}.tsv" if collection else "comparison.tsv"

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
    writer.writerow(HEADER_ROW)
    for row in rows:
        writer.writerow(row)

    return Response(
        buffer.getvalue(),
        mimetype="text/tab-separated-values",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    import argparse

    # 5000 is taken by AirPlay Receiver on macOS, so default to 5001.
    parser = argparse.ArgumentParser(description="Run the collection comparer web app.")
    parser.add_argument("-p", "--port", type=int, default=int(os.environ.get("PORT", 5001)),
                        help="Port to listen on (default: 5001).")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Interface to bind. 0.0.0.0 makes it reachable from other "
                             "devices on your network; use 127.0.0.1 for this machine only.")
    args = parser.parse_args()

    print(f"\n  Open http://localhost:{args.port} in your browser.\n")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
