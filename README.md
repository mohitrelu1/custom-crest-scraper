# custom-crest-scraper

A resilient product-data scraper for arielpremium.com. Reads a list of
product page URLs from a CSV, scrapes only what the client's database
needs — SKU, quantity, price, price code — and writes a clean output
CSV, one row per pricing tier, without ever crashing partway through a
run because one page 404s or times out.

Scraping is requirement-driven: only the four fields above are
extracted. Nothing else (name, dimensions, features, reviews, etc.) is
scraped.

---

## Contents

- [Overview](#overview)
- [Requirements](#requirements)
- [Setup](#setup)
- [Usage](#usage)
- [Input format](#input-format)
- [Output format](#output-format)
- [Error handling](#error-handling)
- [Logging](#logging)
- [Project layout](#project-layout)
- [Before a full run](#before-a-full-run)
- [Troubleshooting](#troubleshooting)

---

## Overview

For each row in `data/data.csv`, the scraper:

1. Fetches the product page (`requests`, with retry-with-backoff on
   transient failures).
2. Parses it (`BeautifulSoup`) for SKU, price tiers, and price code.
3. Writes one row to `output/output.csv` per pricing tier — a product
   with 5 quantity breaks produces 5 rows, all sharing the same SKU and
   price code.
4. Moves on to the next row, no matter what happened.

A single bad URL — a 404, a timeout, a page whose markup doesn't match
what the parser expects — never stops the run. It's logged and skipped.

## Requirements

- Python 3.10+
- Dependencies in `requirements.txt`:

  | package | purpose |
  |---|---|
  | `requests` | HTTP fetching |
  | `beautifulsoup4` + `lxml` | HTML parsing |
  | `colorlog` | colour-coded console logs (optional — falls back to plain text) |
  | `tqdm` | not currently wired into the CLI, but installed for future progress bars |

## Setup

**Windows (VS Code terminal / PowerShell):**

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**macOS / Linux:**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

Always sanity-check on a handful of rows before a full run:

```bash
python main.py --test          # first 3 rows
python main.py --test 20       # first 20 rows
```

Inspect `output/output.csv` and `logs/scraper_YYYYMMDD.log`. Once the
output looks right, run the full file:

```bash
python main.py --all
python main.py --all --delay 1.5    # 1.5s pause between requests (default 1.0s)
```

Progress is saved to `output/output.csv` every 25 rows, so a crash or a
`Ctrl+C` partway through still leaves you with everything scraped so far.

| flag | meaning |
|---|---|
| `--test [N]` | scrape only the first `N` rows (default 3) |
| `--all` | scrape every row in `data.csv` |
| `--delay SECONDS` | pause between requests, default `1.0` |

`--test` and `--all` are mutually exclusive — pick one per run.

## Input format

`data/data.csv`, with a header row containing at least these columns:

| column | meaning |
|---|---|
| `manu_id` | manufacturer ID (not currently used by the scraper, but required in the header) |
| `manu_sku` | fallback SKU, used if the page itself doesn't expose one |
| `product_name` | not currently used by the scraper, but required in the header |
| `prod_page_url` | the URL of the product page to scrape |

Rows with a blank `prod_page_url` are logged and skipped, not treated as errors.

## Output format

`output/output.csv`, one row per pricing tier:

| column | meaning |
|---|---|
| `sku` | Item ID parsed from the page, falling back to `manu_sku` from the input CSV |
| `quantity` | minimum order quantity for this pricing tier |
| `price` | unit price at this quantity |
| `price_code` | pricing-basis code shown next to "Price" on the page — the letter part only (e.g. `C`), with any leading tier-count digit stripped |

A product with multiple quantity breaks produces one row per tier, all
sharing the same `sku` and `price_code`. Example — a product with 5
tiers becomes:

```
sku,quantity,price,price_code
WTV-LG11,30,12.48,C
WTV-LG11,100,11.75,C
WTV-LG11,250,11.28,C
WTV-LG11,500,10.87,C
WTV-LG11,1000,10.45,C
```

Failed products (404s, timeouts, parse errors) are **not** written to
`output.csv` at all — they appear only in the log files. This keeps the
output file clean: every row in it is valid, importable data.

## Error handling

`scraper.py`'s `scrape_product()` never raises. Every failure is caught
and turned into `{"status": "error", "error": "..."}`, and `main.py`'s
loop always continues to the next row. Specifically:

- **404** — detected immediately, not retried (a dead URL will never
  succeed no matter how many times you ask), logged as
  `404 Not Found: <url>`, and skipped.
- **5xx / timeouts / connection drops** — retried up to 3 times with
  exponential backoff (`utils.retry`), since these can be transient.
  If all retries fail, the row is logged and skipped.
- **Unexpected parsing errors** — caught by a general exception handler
  around the whole scrape, logged with the exception message, and
  skipped.

There is no `exit()`, `break`, or bare `raise` anywhere in the per-row
loop — the only way the program stops mid-run is `Ctrl+C`, or a genuine
startup failure (e.g. `data.csv` missing).

At the end of a run, the console prints a summary:

```
============================================================
Total : 3041
Success : 2910
404 : 121
Other Errors : 10
============================================================
```

The full list of failed URLs is written to `logs/errors_YYYYMMDD.log`
rather than the console, so a multi-thousand-row run doesn't flood your
terminal — the console stays a short, readable summary.

## Logging

All logging goes through `logger.py` (no `print()` calls). Every run
writes to three places:

| destination | content |
|---|---|
| console | colour-coded (via `colorlog`, if installed), `INFO` and above, except the per-URL failed list (file-only, see above) |
| `logs/scraper_YYYYMMDD.log` | full `INFO`-and-above log for the day |
| `logs/errors_YYYYMMDD.log` | `ERROR`-and-above only — the fastest place to find what went wrong |

If `colorlog` isn't installed, logging still works — it just falls back
to plain, uncoloured text and prints a one-time warning suggesting
`pip install colorlog`.

## Project layout

```
custom-crest-scraper/
├── data/
│   └── data.csv              # input: manu_id, manu_sku, product_name, prod_page_url
├── output/
│   └── output.csv            # written by main.py — sku, quantity, price, price_code
├── logs/
│   ├── scraper_YYYYMMDD.log  # full run log
│   └── errors_YYYYMMDD.log   # errors only
├── scraper.py                 # ProductScraper.scrape_product(url) - single product extraction
├── utils.py                   # cleaning/parsing helpers, retry decorator
├── logger.py                  # shared logger config, replaces print()
├── main.py                    # CLI entry point, loops over the CSV
└── requirements.txt
```

## Before a full run

Open one product page in a browser, right-click → Inspect, and confirm
these two things still match the site (they're also documented at the
top of `scraper.py`):

1. An "Item ID:" label followed by the SKU text.
2. A pricing table whose header row mentions "Price" or "Qty".

If the site's HTML has changed, update the corresponding `_parse_*`
method in `scraper.py` — the rest of the pipeline (retry, logging, CSV
writing) doesn't need to change.

It's also worth spot-checking a few parsed rows against the live page
after any run — compare the quantity breaks and prices in `output.csv`
against what the product page actually shows.

## Troubleshooting

| symptom | likely cause |
|---|---|
| Every row is missing from `output.csv` | check your internet connection / the site is reachable, or `data.csv`'s `prod_page_url` column is malformed |
| Console shows `colorlog is not installed` warning | cosmetic only — run `pip install colorlog` to fix, or ignore it |
| `price_code` is blank on a page that clearly has one | the page's phrasing doesn't match the expected pattern — check `_parse_price_code()` in `scraper.py` against that page's actual HTML |
| Run stops with `Fatal error: ...` before scraping anything | this is a startup failure (e.g. missing/malformed `data.csv`), not a per-row scrape failure — check the message and fix the input file |
