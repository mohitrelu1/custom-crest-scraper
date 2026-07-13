"""
scraper.py
----------
Everything that talks to a single product page lives here.

Only the fields the client actually needs go into the result: SKU, the
qty/price pairs from the pricing table, and the price code. Nothing else
is scraped (no name, no dimensions, no features) - if a requirement adds
one of those back, add its parser back too, but don't scrape "extra"
fields on spec.

>>> from scraper import ProductScraper
>>> s = ProductScraper()
>>> s.scrape_product("https://www.arielpremium.com/some-product", fallback_sku="ABC123")
{'sku': ..., 'tiers': [(50, 7.57), (200, 7.10), (500, 6.83)],
 'price_code': ..., 'status': 'ok', 'error': ''}

`tiers` is a list of (quantity, price) tuples straight from the pricing
table - main.py is responsible for turning each tuple into its own CSV
row (one row per quantity/price, not one row per product).

BEFORE A FULL RUN: open one product page in Chrome, right-click ->
Inspect, and confirm these two things still match the site:

  1. Somewhere on the page there's a label like "Item ID:" followed by
     the SKU text (used by `_parse_sku`).
  2. The pricing table has rows of quantity/price pairs - look for a
     <table> whose header row mentions "Price" or "Qty"
     (used by `_parse_price_table`).

If the site's HTML has changed, update the `_parse_*` methods below -
the rest of the pipeline (retry, logging, CSV writing) doesn't need to
change.
"""

import re

import requests
from bs4 import BeautifulSoup

from logger import get_logger
from utils import clean_price, clean_text, retry

log = get_logger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


class ScrapeError(Exception):
    """Raised when a page loads but doesn't look like a product page."""


class PageNotFoundError(Exception):
    """
    Raised for a 404 response. Deliberately NOT a requests.RequestException, so the
    @retry decorator on _fetch (which only catches RequestException) lets this one
    through immediately instead of retrying - a 404 will never succeed no matter how
    many times you ask, so retrying it just wastes ~6s per dead URL for nothing.
    Everything else (5xx, timeouts, connection drops) still goes through the normal
    retry-with-backoff path, since those genuinely can be transient.
    """


class ProductScraper:
    """Fetches and parses a single product page at a time."""

    def __init__(self, timeout: int = 15, session: requests.Session = None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def scrape_product(self, url: str, fallback_sku: str = "") -> dict:
        """
        Scrape one product page. Never raises - always returns a dict.

        On success: {"sku", "tiers", "price_code", "status": "ok", "error": ""}
        On failure: {"sku": "", "tiers": [], "price_code": "", "status": "error",
                     "error": "<reason, for the log file only - never written to CSV>"}
        """
        result = {
            "sku": "",
            "tiers": [],
            "price_code": "",
            "status": "error",
            "error": "",
        }

        try:
            soup = self._fetch(url)

            result["sku"] = self._parse_sku(soup) or fallback_sku
            result["tiers"] = self._parse_price_table(soup)
            result["price_code"] = self._parse_price_code(soup)

            result["status"] = "ok"
            log.info(
                "OK  %s (sku=%s, tiers=%d)",
                url, result["sku"], len(result["tiers"]),
            )

        except PageNotFoundError as exc:
            result["error"] = str(exc)
            log.warning("SKIP %s -> %s", url, exc)
            # Nothing more to do for a dead URL - fall through and return this
            # error result so main.py's loop moves on to the next row. This
            # error never reaches the CSV - main.py only logs it.

        except Exception as exc:  # noqa: BLE001 - we want to catch everything else here
            result["error"] = str(exc)
            log.error("FAIL %s -> %s", url, exc)

        return result

    # ------------------------------------------------------------------ #
    # Network
    # ------------------------------------------------------------------ #
    @retry(times=3, delay=2.0, backoff=2.0, exceptions=(requests.RequestException,))
    def _fetch(self, url: str) -> BeautifulSoup:
        response = self.session.get(url, timeout=self.timeout)
        if response.status_code == 404:
            # Fail fast - see PageNotFoundError docstring for why this skips @retry.
            raise PageNotFoundError(f"404 Not Found: {url}")
        response.raise_for_status()  # still retries on 5xx / other errors
        return BeautifulSoup(response.text, "lxml")

    # ------------------------------------------------------------------ #
    # Field parsers - update these if the site markup changes
    # ------------------------------------------------------------------ #
    def _parse_sku(self, soup: BeautifulSoup) -> str:
        # Prefer a dedicated label tag whose ENTIRE text is just "Item ID:" (how the
        # real product's own ID is marked up: <strong>Item ID:</strong> WTV-LP18).
        # This avoids false matches where "Item ID:" shows up mid-sentence in the
        # Features list, referencing a *different* product's SKU, e.g. "Non-woven
        # carrying bag included. See our Item ID: WBA-EL11" - plain running text like
        # that is never the whole content of a <strong>/<b> tag by itself.
        for tag in soup.find_all(["strong", "b"]):
            label = clean_text(tag.get_text())
            if not re.fullmatch(r"item\s*id\s*:?", label, re.I):
                continue
            for candidate in tag.next_elements:
                if getattr(candidate, "parent", None) is tag:
                    continue
                if isinstance(candidate, str) and clean_text(candidate):
                    return clean_text(candidate)

        # Fallback for other site layouts: inline "Item ID: XYZ123" as one text node.
        node = soup.find(string=re.compile(r"Item\s*ID\s*:\s*\S", re.I))
        if node:
            match = re.search(r"Item\s*ID\s*:\s*(\S+)", clean_text(node), re.I)
            if match:
                return match.group(1)
        return ""

    def _parse_price_table(self, soup: BeautifulSoup) -> list:
        """
        Returns a list of (quantity, price) tuples - one tuple per pricing tier,
        e.g. [(50, 7.57), (200, 7.10), (500, 6.83)]. Pairs missing a qty or a
        price are dropped. Each tuple becomes its OWN row in the output CSV -
        a product with 5 tiers produces 5 rows, all sharing the same SKU and
        price_code (see main.py).
        """
        table = None
        for candidate in soup.find_all("table"):
            header_text = clean_text(candidate.get_text()).lower()
            if "price" in header_text and ("qty" in header_text or "quantity" in header_text):
                table = candidate
                break
        if table is None:
            return []

        rows = table.find_all("tr")

        # Layout A (seen on arielpremium.com): one row is ALL quantities, the next row
        # is ALL prices, and the row's own first cell is the label ("Quantity" / "Price
        # (5C) USD") rather than a separate header row. Detect these label rows directly
        # instead of throwing the whole row away just because it contains "qty"/"price".
        qty_row, price_row = None, None
        for row in rows:
            cells = [clean_text(c.get_text()) for c in row.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if not cells:
                continue
            label = cells[0]
            if qty_row is None and re.search(r"^qty$|quantity", label, re.I):
                qty_row = cells[1:]
            elif price_row is None and re.search(r"price", label, re.I):
                price_row = cells[1:]

        if qty_row and price_row:
            pairs = [
                (clean_price(q), clean_price(p))
                for q, p in zip(qty_row, price_row)
            ]
            return [(q, p) for q, p in pairs if q is not None and p is not None]

        # Layout B (fallback): one qty/price pair per row, or two columns per row,
        # with a genuine header row (e.g. "Qty | Price") separate from the data rows.
        qty_cells, price_cells = [], []
        for row in rows:
            cells = [clean_text(c.get_text()) for c in row.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if not cells:
                continue
            is_header_row = all(re.search(r"qty|quantity|price", c, re.I) for c in cells)
            if is_header_row:
                continue
            for cell in cells:
                if re.fullmatch(r"[\d,]+", cell):
                    qty_cells.append(cell)
                elif re.search(r"\d", cell):
                    price_cells.append(cell)

        pairs = []
        for qty_text, price_text in zip(qty_cells, price_cells):
            qty = clean_price(qty_text)
            price = clean_price(price_text)
            if qty is not None and price is not None:
                pairs.append((qty, price))
        return pairs

    def _parse_price_code(self, soup: BeautifulSoup) -> str:
        # Old regex only matched a single 1-4 char alnum token, so it caught "5C"
        # but missed "BBB" (3 letters, still fine actually) and, more importantly,
        # combo/range codes like "A to C&C" that include spaces, "&", and "-".
        node = soup.find(string=re.compile(r"Price\s*[:\-]?\s*\(?[A-Za-z0-9]", re.I))
        if not node:
            return ""
        text = clean_text(node)

        # Preferred case: something in parens right after "Price" - e.g.
        # "Price (5C) USD", "Price(BBB)USD", "Price (A to C&C)". Parens are an
        # unambiguous boundary, so allow letters/digits/spaces/&/- inside them
        # rather than a fixed-length token.
        match = re.search(r"Price\s*[:\-]?\s*\(([A-Za-z0-9 &\-]{1,24})\)", text, re.I)
        if match:
            return match.group(1).strip().upper()

        # No parens - "Price: 3B", "Price - A to C&C". Capture a run of code-ish
        # characters right after "Price:"/"Price-", capped at 24 chars so it can't
        # run away and swallow an unrelated sentence.
        match = re.search(
            r"Price\s*[:\-]\s*([A-Za-z0-9](?:[A-Za-z0-9 &\-]{0,22}[A-Za-z0-9])?)",
            text, re.I,
        )
        if match:
            code = match.group(1).strip()
            # Strip a trailing currency/unit word that isn't actually part of the
            # code, e.g. "3B USD" -> "3B".
            code = re.sub(r"\s+(USD|EACH|PER\s*UNIT)$", "", code, flags=re.I).strip()
            return code.upper()

        return ""