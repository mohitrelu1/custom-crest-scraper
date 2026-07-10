"""
main.py
-------
CLI entry point. Reads data/data.csv, scrapes each product page, writes
data/output/output.csv, and logs everything via logger.py.

Usage:
    python main.py --test          # first 3 rows only
    python main.py --test 10       # first 10 rows
    python main.py --all           # every row in data.csv
    python main.py --all --delay 1.5   # 1.5s pause between requests
"""

import argparse
import csv
import os
import time

from logger import get_logger
from scraper import ProductScraper

log = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_CSV = os.path.join(BASE_DIR, "data", "data.csv")
OUTPUT_CSV = os.path.join(BASE_DIR, "output", "output.csv")

OUTPUT_FIELDS = [
    "url", "sku", "name", "price_tiers", "min_price",
    "price_code", "dimensions", "features", "status", "error",
]

SAVE_EVERY = 25  # write partial progress to disk every N rows


def parse_args():
    parser = argparse.ArgumentParser(description="Scrape product pages listed in data/data.csv")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--test", nargs="?", const=3, type=int, metavar="N",
        help="Scrape only the first N rows (default 3). Good for a sanity check.",
    )
    group.add_argument("--all", action="store_true", help="Scrape every row in data.csv")
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="Seconds to wait between requests (default 1.0). Be polite to the server.",
    )
    return parser.parse_args()


def load_rows(limit: int = None) -> list:
    if not os.path.exists(DATA_CSV):
        raise FileNotFoundError(f"Input CSV not found: {DATA_CSV}")

    with open(DATA_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    required = {"manu_id", "manu_sku", "product_name", "prod_page_url"}
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"data.csv is missing expected columns: {missing}")

    return rows[:limit] if limit else rows


def save_results(results: list) -> None:
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(results)


def print_summary(results: list) -> None:
    ok = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] != "ok"]

    # Split failures into "404 Not Found" (dead product page - expected, not a bug)
    # vs everything else (timeouts, 5xx, parsing errors, etc. - worth a closer look).
    not_found = [r for r in failed if r["error"].startswith("404")]
    other_errors = [r for r in failed if not r["error"].startswith("404")]

    if failed:
        # file_only=True: this goes to errors.log (for later inspection) but is
        # filtered out of the console, so a long run's terminal output stays
        # the short summary below instead of a wall of repeated URLs.
        log.error("Failed URLs:", extra={"file_only": True})
        for row in failed:
            log.error("  %s -> %s", row["url"], row["error"], extra={"file_only": True})

    log.info("=" * 60)
    log.info("Total : %d", len(results))
    log.info("Success : %d", len(ok))
    log.info("404 : %d", len(not_found))
    log.info("Other Errors : %d", len(other_errors))
    log.info("=" * 60)


def main():
    args = parse_args()
    limit = args.test if args.test else None

    log.info("Loading rows from %s (limit=%s)", DATA_CSV, limit or "all")
    rows = load_rows(limit)
    log.info("Loaded %d row(s) to scrape", len(rows))

    scraper = ProductScraper()
    results = []

    for i, row in enumerate(rows, start=1):
        url = (row.get("prod_page_url") or "").strip()
        fallback_sku = (row.get("manu_sku") or "").strip()

        if not url:
            log.warning("Row %d has no prod_page_url - skipping", i)
            continue

        log.info("[%d/%d] scraping %s", i, len(rows), url)
        result = scraper.scrape_product(url, fallback_sku=fallback_sku)
        results.append(result)

        if i % SAVE_EVERY == 0:
            save_results(results)
            log.info("Progress saved (%d rows so far)", i)

        if i < len(rows):
            time.sleep(args.delay)

    save_results(results)
    print_summary(results)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.warning("Interrupted by user - partial output.csv (if any) is still on disk")
    except Exception as exc:  # noqa: BLE001
        log.critical("Fatal error: %s", exc)
        raise
