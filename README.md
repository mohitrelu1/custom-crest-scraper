# custom-crest-scraper

Scrapes product name, SKU, price tiers, and spec details from
arielpremium.com product pages listed in `data/data.csv`.

## Setup (Windows, VS Code terminal)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Project layout

```
custom-crest-scraper/
├── data/
│   └── data.csv          # input: manu_id, manu_sku, product_name, prod_page_url
├── output/
│   └── output.csv        # written by main.py
├── logs/
│   ├── scraper_YYYYMMDD.log   # full run log
│   └── errors_YYYYMMDD.log    # ERROR-level only, i.e. failed URLs
├── scraper.py             # ProductScraper class - single product extraction
├── utils.py                # cleaning / parsing helpers, retry decorator
├── logger.py                # shared colour-coded logger, replaces print()
├── main.py                  # CLI entry point, loops over the CSV
└── requirements.txt
```

## Run it

Always sanity-check on a handful of rows first:

```
python main.py --test          # first 3 rows
python main.py --test 10       # first 10 rows
```

Check `output/output.csv` and `logs/scraper_YYYYMMDD.log`. Once it looks right:

```
python main.py --all           # all ~3000 rows
```

The run saves partial progress every 25 rows, so if it crashes or you
Ctrl+C partway through, `output/output.csv` still has everything
scraped up to that point. Failed URLs are also collected in
`logs/errors_YYYYMMDD.log` and printed as a summary at the end of the run.

## Output columns

| column | meaning |
|---|---|
| `url` | product page URL |
| `sku` | Item ID (falls back to the CSV's manu_sku if not found on page) |
| `name` | product name |
| `price_tiers` | `qty:price` pairs separated by `\|`, e.g. `50:7.57\|200:7.1\|500:6.83` |
| `min_price` | lowest tier price (highest quantity break) |
| `price_code` | the pricing-basis code shown next to "Price" on the page, e.g. `5C` |
| `dimensions` | product dimensions text |
| `features` | bullet features, joined by `; ` |
| `status` | `ok` or `error` |
| `error` | error message if `status == error` |

## Before running the full 3000 rows

Open one product page in Chrome, right-click → Inspect, and confirm
the pricing table / "Item ID:" label / Features heading match what
`scraper.py` expects (see the docstring at the top of `scraper.py`).
The selectors are written to walk the page structure rather than rely
on guessed CSS class names, but the site can change, and it's worth
one manual check before a long run.

## If they later ask for Selenium / Playwright

Only needed if pages turn out to be JS-rendered (content that doesn't
appear in `requests.get().text`). Test that on one URL before adding
the extra complexity:

```
pip install selenium
# or
pip install playwright && playwright install
```
