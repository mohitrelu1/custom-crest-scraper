"""
scraper.py
----------
Everything that talks to a single product page lives here.

>>> from scraper import ProductScraper
>>> s = ProductScraper()
>>> s.scrape_product("https://www.arielpremium.com/some-product", fallback_sku="ABC123")
{'url': ..., 'sku': ..., 'name': ..., 'price_tiers': ..., 'min_price': ...,
 'price_code': ..., 'dimensions': ..., 'features': ..., 'status': 'ok', 'error': ''}

BEFORE A FULL RUN: open one product page in Chrome, right-click ->
Inspect, and confirm these three things still match the site:

  1. Somewhere on the page there's a label like "Item ID:" followed by
     the SKU text (used by `_parse_sku`).
  2. The pricing table has rows of quantity/price pairs - look for a
     <table> whose header row mentions "Price" or "Qty"
     (used by `_parse_price_table`).
  3. There's a heading containing the word "Features" followed by a
     <ul> of bullet points (used by `_parse_features`).

If the site's HTML has changed, update the three `_parse_*` methods
below - the rest of the pipeline (retry, logging, CSV writing) doesn't
need to change.
"""

import re

import requests
from bs4 import BeautifulSoup

from logger import get_logger
from utils import clean_price, clean_text, min_price_from_tiers, parse_price_tiers, retry

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
        Scrape one product page. Never raises - always returns a dict,
        with status='error' and an 'error' message if something went wrong.
        """
        result = {
            "url": url,
            "sku": "",
            "name": "",
            "price_tiers": "",
            "min_price": "",
            "price_code": "",
            "dimensions": "",
            "features": "",
            "status": "error",
            "error": "",
        }

        try:
            soup = self._fetch(url)

            result["name"] = self._parse_name(soup)
            result["sku"] = self._parse_sku(soup) or fallback_sku

            tiers = self._parse_price_table(soup)
            result["price_tiers"] = tiers
            result["min_price"] = min_price_from_tiers(tiers)

            result["price_code"] = self._parse_price_code(soup)
            result["dimensions"] = self._parse_dimensions(soup)
            result["features"] = self._parse_features(soup)

            result["status"] = "ok"
            n_tiers = len(result["price_tiers"].split("|")) if result["price_tiers"] else 0
            n_features = len(result["features"].split("; ")) if result["features"] else 0
            log.info(
                "OK  %s (sku=%s, price_tiers=%d, features=%d, dimensions=%s)",
                url, result["sku"], n_tiers, n_features,
                "found" if result["dimensions"] else "missing",
            )

        except PageNotFoundError as exc:
            result["error"] = str(exc)
            log.warning("SKIP %s -> %s", url, exc)
            # Nothing more to do for a dead URL - fall through and return this
            # error result so main.py's loop moves on to the next row.

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
    # Text that shows up as a heading on nearly every page of this site but is never
    # the actual product name - a raw, un-rendered template placeholder for whichever
    # Bootstrap modal happens to be first in the DOM (color disclaimer, login, etc).
    _NAME_BOILERPLATE = {"modal title"}

    def _parse_name(self, soup: BeautifulSoup) -> str:
        # Prefer <h1>, but this site sometimes has only one <h1> on the entire page
        # and it's the modal placeholder above, with the real name sitting in <h2>.
        for tag_name in ("h1", "h2", "h3"):
            for tag in soup.find_all(tag_name):
                text = clean_text(tag.get_text())
                if text and text.lower() not in self._NAME_BOILERPLATE:
                    return text
        return ""

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

    def _parse_price_table(self, soup: BeautifulSoup) -> str:
        table = None
        for candidate in soup.find_all("table"):
            header_text = clean_text(candidate.get_text()).lower()
            if "price" in header_text and ("qty" in header_text or "quantity" in header_text):
                table = candidate
                break
        if table is None:
            return ""

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
            return parse_price_tiers(pairs)

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
            pairs.append((qty, price))
        return parse_price_tiers(pairs)

    def _parse_price_code(self, soup: BeautifulSoup) -> str:
        # Matches "Price: 5C", "Price - 5C", and the arielpremium.com style
        # "Price (5C) USD" / "Price(5C)USD".
        node = soup.find(string=re.compile(r"Price\s*[:\-]?\s*\(?[A-Z0-9]{1,4}\)?", re.I))
        if not node:
            return ""
        match = re.search(r"Price\s*[:\-]?\s*\(?([A-Z0-9]{1,4})\)?", clean_text(node), re.I)
        return match.group(1).upper() if match else ""

    _HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "strong", "b")

    def _parse_dimensions(self, soup: BeautifulSoup) -> str:
        # Case 1: inline "Dimensions: 5.5in x 8.5in" as one text node.
        node = soup.find(string=re.compile(r"Dimensions?\s*:", re.I))
        if node:
            text = clean_text(node)
            match = re.search(r"Dimensions?\s*:\s*(.+)", text, re.I)
            if match and match.group(1).strip():
                return match.group(1).strip()

        # Case 2: bare "Dimensions" heading (e.g. <h5>Dimensions</h5>) with the value
        # in the next element/text node, and no colon anywhere.
        heading = soup.find(
            lambda tag: tag.name in ProductScraper._HEADING_TAGS
            and re.fullmatch(r"dimensions?", tag.get_text(strip=True), re.I)
        )
        if heading:
            # Walk forward and skip any text that's still part of the heading tag
            # itself (find_next()/next_elements() visit a tag's own children first).
            for candidate in heading.next_elements:
                if getattr(candidate, "parent", None) is heading:
                    continue
                if isinstance(candidate, str) and clean_text(candidate):
                    return clean_text(candidate)

        return ""

    def _parse_features(self, soup: BeautifulSoup) -> str:
        heading = soup.find(
            lambda tag: tag.name in ProductScraper._HEADING_TAGS
            and "feature" in tag.get_text(strip=True).lower()
        )
        if not heading:
            return ""
        ul = heading.find_next("ul")
        if not ul:
            return ""
        bullets = [clean_text(li.get_text()) for li in ul.find_all("li")]
        return "; ".join(b for b in bullets if b)