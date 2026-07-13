"""
utils.py
--------
Small, dependency-light helper functions used by scraper.py and main.py.
Nothing in here touches the network - keep it pure/testable.
"""

import functools
import re
import time

from logger import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Retry decorator
# --------------------------------------------------------------------------- #
def retry(times: int = 3, delay: float = 2.0, backoff: float = 2.0, exceptions=(Exception,)):
    """
    Retry a function on failure with exponential backoff.

    @retry(times=3, delay=2, backoff=2)
    def fetch(url): ...

    Attempt 1 fails -> wait 2s
    Attempt 2 fails -> wait 4s
    Attempt 3 fails -> raise the last exception
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exc = None
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == times:
                        break
                    log.warning(
                        "%s failed (attempt %d/%d): %s - retrying in %.1fs",
                        func.__name__, attempt, times, exc, current_delay,
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff
            raise last_exc
        return wrapper
    return decorator


# --------------------------------------------------------------------------- #
# Text / price cleaning
# --------------------------------------------------------------------------- #
def clean_text(value: str) -> str:
    """Collapse whitespace/newlines, strip, return '' for None."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def clean_price(value: str):
    """
    '$7.57' -> 7.57 ; '7,57' -> 7.57 ; '' / None / garbage -> None
    Keeps only digits and one decimal separator.
    """
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def format_qty(qty) -> str:
    """100.0 -> '100' ; 12.5 -> '12.5'. Keeps quantity columns clean in the CSV."""
    return str(int(qty)) if float(qty).is_integer() else str(qty)


# --------------------------------------------------------------------------- #
# Price code splitting
# --------------------------------------------------------------------------- #
def split_price_code(raw_code: str):
    """
    The site writes the price code as '<tier count><letter code>', e.g. '5C'
    for a 5-tier table, '2C' for a 2-tier table. The leading digit is just the
    page's own tally of how many price breaks follow - it is NOT part of the
    code the client's database wants. Each output row already corresponds to
    one scraped (qty, price) tier, so that count is redundant; only the
    letter part ('C') belongs in the price_code column.

    Returns (tier_count, letter_code):
        '5C'        -> (5, 'C')
        '2C'        -> (2, 'C')
        'A to C&C'  -> (None, 'A to C&C')   # no leading digit - nothing to split
        ''          -> (None, '')

    tier_count is returned (not discarded) so the caller can cross-check it
    against the number of tiers actually parsed from the price table and log
    a warning on mismatch - the two numbers should always agree, and a
    disagreement means either the price table or the code text was
    misparsed and is worth a human look.
    """
    if not raw_code:
        return None, ""
    match = re.match(r"(\d+)\s*(.*)", raw_code)
    if not match:
        return None, raw_code
    count_str, letters = match.groups()
    letters = letters.strip()
    if not letters:
        # Digits only, nothing after them - not a real "<count><code>" pattern,
        # so there's no letter code to extract. Leave the raw value alone
        # rather than guessing.
        return None, raw_code
    return int(count_str), letters