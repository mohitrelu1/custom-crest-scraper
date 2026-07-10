# custom-crest-scraper

A resilient product-data scraper for arielpremium.com. Reads a list of
product page URLs from a CSV, scrapes name / SKU / pricing / dimensions /
features from each one, and writes a clean output CSV — without ever
crashing partway through a run because one page 404s or times out.

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
2. Parses it (`BeautifulSoup`) for name, SKU, price tiers, price code,
   dimensions, and features.
3. Writes one row to `output/output.csv` — either the parsed data
   (`status = ok`) or an error message (`status = error`).
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
| `prod_page_url` | the URL to scrape |

Rows with a blank `prod_page_url` are logged and skipped, not treated as errors.

## Output format

`output/output.csv`, one row per input row:

| column | meaning |
|---|---|
| `url` | product page URL |
| `sku` | Item ID parsed from the page, falling back to `manu_sku` from the input CSV |
| `name` | product name |
| `price_tiers` | `qty:price` pairs joined by `\|`, e.g. `50:7.57\|200:7.1\|500:6.83` |
| `min_price` | lowest tier price (best quantity break) |
| `price_code` | pricing-basis code shown next to "Price" on the page — handles single codes (`5C`), letter-only codes (`BBB`), and combo/range codes (`A to C&C`) |
| `dimensions` | product dimensions text |
| `features` | bullet features, joined by `; ` |
| `status` | `ok` or `error` |
| `error` | error message when `status = error`, otherwise blank |

A failed row still gets a full CSV row — `status = error`, `error` filled
in, every other column blank — never a skipped/empty line and never a
stopped program.

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
│   └── output.csv            # written by main.py
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
these three things still match the site (they're also documented at the
top of `scraper.py`):

1. An "Item ID:" label followed by the SKU text.
2. A pricing table whose header row mentions "Price" or "Qty".
3. A heading containing "Features" followed by a `<ul>` of bullet points.

If the site's HTML has changed, update the corresponding `_parse_*`
method in `scraper.py` — the rest of the pipeline (retry, logging, CSV
writing) doesn't need to change.

It's also worth spot-checking a few parsed rows against the live page
after any run, particularly `features` — the parser takes whatever `<ul>`
immediately follows the first "Features" heading it finds, so a page
whose layout puts something else there could get picked up too.

## Troubleshooting

| symptom | likely cause |
|---|---|
| Every row is `error` | check your internet connection / the site is reachable, or `data.csv`'s `prod_page_url` column is malformed |
| Console shows `colorlog is not installed` warning | cosmetic only — run `pip install colorlog` to fix, or ignore it |
| `price_code` is blank on a page that clearly has one | the page's phrasing doesn't match "Price ... (CODE)" or "Price: CODE" — check `_parse_price_code()` in `scraper.py` against that page's actual HTML |
| Run stops with `Fatal error: ...` before scraping anything | this is a startup failure (e.g. missing/malformed `data.csv`), not a per-row scrape failure — check the message and fix the input file |
