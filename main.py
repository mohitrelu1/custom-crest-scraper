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
from utils import format_qty

log = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_CSV = os.path.join(BASE_DIR, "data", "data.csv")
OUTPUT_CSV = os.path.join(BASE_DIR, "output", "output.csv")

# Only what the client's database needs. Nothing else goes in the CSV -
# no status, no error, no fields that weren't asked for.
OUTPUT_FIELDS = ["sku", "quantity", "price", "price_code"]

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


def save_results(csv_rows: list) -> None:
    """Write the CSV - only successfully scraped business data, one row per
    quantity/price tier. Failed products never reach this list at all
    (see main()); there is no status/error column to filter out here."""
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(csv_rows)


def print_summary(total: int, success_count: int, failed: list) -> None:
    """
    `failed` is a list of (url, error_message) tuples - covers both actual
    scrape failures (404s, timeouts, exceptions) and products that loaded
    fine but had no usable price tiers to write. None of this ever goes in
    the CSV; it's log-only.
    """
    # Split failures into "404 Not Found" (dead product page - expected, not a bug)
    # vs everything else (timeouts, 5xx, parsing errors, etc. - worth a closer look).
    not_found = [f for f in failed if f[1].startswith("404")]
    other_errors = [f for f in failed if not f[1].startswith("404")]

    if failed:
        # file_only=True: this goes to errors.log (for later inspection) but is
        # filtered out of the console, so a long run's terminal output stays
        # the short summary below instead of a wall of repeated URLs.
        log.error("Failed URLs:", extra={"file_only": True})
        for url, error in failed:
            log.error("  %s -> %s", url, error, extra={"file_only": True})

    log.info("=" * 60)
    log.info("Total : %d", total)
    log.info("Success : %d", success_count)
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

    csv_rows = []       # what actually gets written to output.csv - clean business data only
    failed = []          # (url, error) tuples - log file only, never CSV
    total = 0
    success_count = 0

    for i, row in enumerate(rows, start=1):
        url = (row.get("prod_page_url") or "").strip()
        fallback_sku = (row.get("manu_sku") or "").strip()

        if not url:
            log.warning("Row %d has no prod_page_url - skipping", i)
            continue

        total += 1
        log.info("[%d/%d] scraping %s", i, len(rows), url)
        result = scraper.scrape_product(url, fallback_sku=fallback_sku)

        if result["status"] == "ok" and result["tiers"]:
            # One CSV row per quantity/price tier - a product with 5 tiers
            # produces 5 rows, all sharing the same sku and price_code.
            for qty, price in result["tiers"]:
                csv_rows.append({
                    "sku": result["sku"],
                    "quantity": format_qty(qty),
                    "price": price,
                    "price_code": result["price_code"],
                })
            success_count += 1
        elif result["status"] == "ok":
            # Page loaded fine but no pricing table matched - nothing valid
            # to put in the CSV, so log it instead of writing a blank/error row.
            log.warning("No price tiers found for %s (sku=%s)", url, result["sku"])
            failed.append((url, "No price tiers found"))
        else:
            # 404 / timeout / parse exception - logged only, never written to the CSV.
            failed.append((url, result["error"]))

        if i % SAVE_EVERY == 0:
            save_results(csv_rows)
            log.info("Progress saved (%d CSV rows so far)", len(csv_rows))

        if i < len(rows):
            time.sleep(args.delay)

    save_results(csv_rows)
    print_summary(total, success_count, failed)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.warning("Interrupted by user - partial output.csv (if any) is still on disk")
    except Exception as exc:  # noqa: BLE001
        log.critical("Fatal error: %s", exc)
        raise