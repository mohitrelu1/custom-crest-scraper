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


def parse_price_tiers(pairs: list) -> str:
    """
    Turn [(50, 7.57), (200, 7.10), (500, 6.83)] into 'qty:price' joined by '|'.
    Skips any pair where qty or price is missing.
    """
    parts = []
    for qty, price in pairs:
        if qty is None or price is None:
            continue
        qty_str = str(int(qty)) if float(qty).is_integer() else str(qty)
        parts.append(f"{qty_str}:{price}")
    return "|".join(parts)


def min_price_from_tiers(tiers_str: str):
    """'50:7.57|200:7.1|500:6.83' -> 6.83 (lowest price, i.e. best break)."""
    if not tiers_str:
        return None
    prices = []
    for pair in tiers_str.split("|"):
        if ":" not in pair:
            continue
        _, price = pair.split(":", 1)
        price = clean_price(price)
        if price is not None:
            prices.append(price)
    return min(prices) if prices else None
