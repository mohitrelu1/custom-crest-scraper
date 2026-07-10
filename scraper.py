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
            log.info("OK  %s (sku=%s)", url, result["sku"])

        except Exception as exc:  # noqa: BLE001 - we want to catch everything here
            result["error"] = str(exc)
            log.error("FAIL %s -> %s", url, exc)

        return result

    # ------------------------------------------------------------------ #
    # Network
    # ------------------------------------------------------------------ #
    @retry(times=3, delay=2.0, backoff=2.0, exceptions=(requests.RequestException,))
    def _fetch(self, url: str) -> BeautifulSoup:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return BeautifulSoup(response.text, "lxml")

    # ------------------------------------------------------------------ #
    # Field parsers - update these if the site markup changes
    # ------------------------------------------------------------------ #
    def _parse_name(self, soup: BeautifulSoup) -> str:
        h1 = soup.find("h1")
        return clean_text(h1.get_text()) if h1 else ""

    def _parse_sku(self, soup: BeautifulSoup) -> str:
        node = soup.find(string=re.compile(r"Item\s*ID\s*:", re.I))
        if not node:
            return ""
        text = clean_text(node)
        match = re.search(r"Item\s*ID\s*:\s*(\S+)", text, re.I)
        if match:
            return match.group(1)
        # label and value might be in a sibling element instead of the same string
        parent = node.parent
        sibling = parent.find_next(string=True) if parent else None
        return clean_text(sibling) if sibling else ""

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
        qty_cells, price_cells = [], []
        for row in rows:
            cells = [clean_text(c.get_text()) for c in row.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if not cells:
                continue
            header_row = any(re.search(r"qty|quantity|price", c, re.I) for c in cells)
            if header_row:
                continue
            # assume alternating qty/price layout across two rows or two columns
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
        node = soup.find(string=re.compile(r"Price\s*[:\-]?\s*[A-Z0-9]{1,4}\b"))
        if not node:
            return ""
        match = re.search(r"Price\s*[:\-]?\s*([A-Z0-9]{1,4})\b", clean_text(node))
        return match.group(1) if match else ""

    def _parse_dimensions(self, soup: BeautifulSoup) -> str:
        node = soup.find(string=re.compile(r"Dimensions?\s*:", re.I))
        if not node:
            return ""
        text = clean_text(node)
        match = re.search(r"Dimensions?\s*:\s*(.+)", text, re.I)
        return match.group(1).strip() if match else text

    def _parse_features(self, soup: BeautifulSoup) -> str:
        heading = soup.find(
            lambda tag: tag.name in ("h2", "h3", "h4", "strong", "b")
            and "feature" in tag.get_text(strip=True).lower()
        )
        if not heading:
            return ""
        ul = heading.find_next("ul")
        if not ul:
            return ""
        bullets = [clean_text(li.get_text()) for li in ul.find_all("li")]
        return "; ".join(b for b in bullets if b)
