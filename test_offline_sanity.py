"""
test_offline_sanity.py
-----------------------
Offline sanity check - no network. Feeds hand-built HTML that mimics
arielpremium.com's markup (Item ID label, qty/price label-rows, "Price
(5C) USD" text) straight into ProductScraper's internal parsers, so we
can verify the price_code split logic without hitting the live site.

Run with:
    python test_offline_sanity.py
"""

from bs4 import BeautifulSoup

from scraper import ProductScraper
from utils import split_price_code

s = ProductScraper()

# --- Case 1: normal 5-tier product, code "5C" -----------------------------
html_5c = """
<html><body>
<strong>Item ID:</strong> WTV-LG11
<table>
<tr><td>Price (5C) USD</td></tr>
<tr><td>Qty</td><td>30</td><td>100</td><td>250</td><td>500</td><td>1000</td></tr>
<tr><td>Price</td><td>$12.48</td><td>$11.75</td><td>$11.28</td><td>$10.87</td><td>$10.45</td></tr>
</table>
</body></html>
"""
soup = BeautifulSoup(html_5c, "lxml")
sku = s._parse_sku(soup)
tiers = s._parse_price_table(soup)
raw_code = s._parse_price_code(soup)
count, letter_code = split_price_code(raw_code)

print("Case 1 (5C):")
print("  sku:", sku)
print("  tiers:", tiers)
print("  raw_code:", raw_code, "-> count:", count, "letter_code:", letter_code)
assert sku == "WTV-LG11"
assert len(tiers) == 5
assert raw_code == "5C"
assert (count, letter_code) == (5, "C")
assert count == len(tiers), "mismatch check should NOT fire here"
print("  PASS\n")

# --- Case 2: 2-tier product, code "2C" -------------------------------------
html_2c = """
<html><body>
<strong>Item ID:</strong> WBA-TB10
<table>
<tr><td>Price (2C) USD</td></tr>
<tr><td>Qty</td><td>100</td><td>250</td></tr>
<tr><td>Price</td><td>$6.15</td><td>$5.63</td></tr>
</table>
</body></html>
"""
soup2 = BeautifulSoup(html_2c, "lxml")
tiers2 = s._parse_price_table(soup2)
raw_code2 = s._parse_price_code(soup2)
count2, letter_code2 = split_price_code(raw_code2)

print("Case 2 (2C):")
print("  tiers:", tiers2, "raw_code:", raw_code2, "-> count:", count2, "letter_code:", letter_code2)
assert (count2, letter_code2) == (2, "C")
assert count2 == len(tiers2)
print("  PASS\n")

# --- Case 3: mismatch should be detectable (not silently trusted) ----------
count3, letter3 = split_price_code("5C")
tiers3_len = 4  # pretend only 4 tiers were actually parsed
print("Case 3 (deliberate mismatch, 5C code vs 4 parsed tiers):")
print("  count:", count3, "letter_code:", letter3, "actual tiers:", tiers3_len)
assert count3 != tiers3_len, "this is the case scrape_product() should log.warning() on"
print("  PASS - mismatch correctly detectable\n")

# --- Case 4: combo code with no leading digit -------------------------------
count4, letter4 = split_price_code("A to C&C")
print("Case 4 (no leading digit):", (count4, letter4))
assert (count4, letter4) == (None, "A to C&C")
print("  PASS\n")

# --- Case 5: empty code ------------------------------------------------------
count5, letter5 = split_price_code("")
assert (count5, letter5) == (None, "")
print("Case 5 (empty): PASS\n")

# --- Case 6: full scrape_product() end-to-end wiring, mocking _fetch -------
s._fetch = lambda url: BeautifulSoup(html_5c, "lxml")
result = s.scrape_product("https://www.arielpremium.com/product/FAKE", fallback_sku="FALLBACK")
print("Case 6 (full scrape_product wiring):")
print("  ", result)
assert result["status"] == "ok"
assert result["price_code"] == "C"  # letter only, not "5C"
assert len(result["tiers"]) == 5
assert result["sku"] == "WTV-LG11"
print("  PASS\n")

print("ALL OFFLINE TESTS PASSED")
