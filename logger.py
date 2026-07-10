"""
logger.py
---------
One shared logger for the whole project. Import `get_logger()` anywhere
instead of using print().

- Console output is colour-coded by level (needs `colorlog`; falls back
  to plain logging if it isn't installed).
- File output goes to logs/scraper_YYYYMMDD.log (plain text, no colour
  codes, so it stays readable in a text editor).
- A second file, logs/errors_YYYYMMDD.log, receives ERROR and above
  only, so failed URLs are easy to find without wading through the
  full run log.

Usage:
    from logger import get_logger
    log = get_logger(__name__)
    log.info("starting up")
    log.error("failed to fetch %s", url)
"""

import logging
import os
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

try:
    import colorlog
    _HAS_COLORLOG = True
except ImportError:
    _HAS_COLORLOG = False

_CONFIGURED = False


class _FileOnlyFilter(logging.Filter):
    """
    Blocks console output for any record logged with extra={"file_only": True}.
    File handlers don't get this filter, so those records still land in
    scraper_YYYYMMDD.log / errors_YYYYMMDD.log as normal - they just don't
    clutter the terminal. Used for the per-URL failed list in main.py, so a
    3000+ row run's console output stays a short summary instead of a wall
    of repeated URLs (which are already visible in errors.log).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "file_only", False)


def _build_console_handler() -> logging.Handler:
    """Console handler: colour-coded if colorlog is available, plain otherwise."""
    handler = logging.StreamHandler()
    handler.addFilter(_FileOnlyFilter())

    if _HAS_COLORLOG:
        formatter = colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )

    handler.setFormatter(formatter)
    return handler


def _build_file_handler(path: str, level: int) -> logging.Handler:
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler


def _configure_root() -> None:
    """Attach handlers to the root logger exactly once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    root.addHandler(_build_console_handler())
    root.addHandler(
        _build_file_handler(os.path.join(LOG_DIR, f"scraper_{stamp}.log"), logging.INFO)
    )
    root.addHandler(
        _build_file_handler(os.path.join(LOG_DIR, f"errors_{stamp}.log"), logging.ERROR)
    )

    if not _HAS_COLORLOG:
        root.warning(
            "colorlog is not installed - console logs will be plain text. "
            "Run: pip install colorlog"
        )

    # requests/urllib3 debug logs are noisy and not useful for this project
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str = "scraper") -> logging.Logger:
    """Return a module-level logger. Safe to call repeatedly."""
    _configure_root()
    return logging.getLogger(name)
