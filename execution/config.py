"""Börsihai configuration — constants, environment variables, and logging setup."""
import logging
import os
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

# ─── Environment Variables ────────────────────────────────────────────

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SYSTEMD_SERVICE_NAME = os.getenv("SYSTEMD_SERVICE_NAME", "borsihai")

# ─── Trading Constants ────────────────────────────────────────────────

MAX_POSITIONS = 5
POSITION_SIZE_PCT = 0.20       # 10% of portfolio per position
ATR_MULTIPLIER = 2.0           # SL = Entry +/- (ATR * 2.0)
TP1_RR_RATIO = 1.5             # TP1 at 1.5x risk
TP_STEP_RR = 1.0               # Subsequent TP increments (e.g. TP2 = TP1 + 1.0R)
DEFAULT_PORTFOLIO_BALANCE = 25000.0
DEFAULT_TIMEFRAME = "1h"       # Default entry timeframe

# ─── Timeframe Configuration ──────────────────────────────────────────

# Maps normalised user input → ccxt timeframe string
VALID_TIMEFRAMES = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "12h": "12h",
    "1d": "1d",
    "3d": "3d",
    "1w": "1w",
}

# For each entry timeframe, define: trend filter TF, position monitor TF, and scan interval in minutes
TIMEFRAME_PAIRINGS = {
    "1m":  {"trend": "5m",  "monitor": "1m",  "scan_interval": 1},
    "3m":  {"trend": "15m", "monitor": "1m",  "scan_interval": 3},
    "5m":  {"trend": "15m", "monitor": "5m",  "scan_interval": 5},
    "15m": {"trend": "1h",  "monitor": "5m",  "scan_interval": 15},
    "30m": {"trend": "2h",  "monitor": "5m",  "scan_interval": 30},
    "1h":  {"trend": "4h",  "monitor": "5m",  "scan_interval": 60},
    "2h":  {"trend": "8h",  "monitor": "15m", "scan_interval": 120},
    "4h":  {"trend": "1d",  "monitor": "15m", "scan_interval": 240},
    "6h":  {"trend": "1d",  "monitor": "30m", "scan_interval": 360},
    "12h": {"trend": "3d",  "monitor": "1h",  "scan_interval": 720},
    "1d":  {"trend": "1w",  "monitor": "1h",  "scan_interval": 1440},
    "3d":  {"trend": "1w",  "monitor": "4h",  "scan_interval": 4320},
    "1w":  {"trend": "1w",  "monitor": "1d",  "scan_interval": 10080},
}

# Aliases → canonical form used in VALID_TIMEFRAMES
_TF_ALIASES = {
    # Minutes
    "1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m",
    "1minute": "1m", "5minutes": "5m", "15minutes": "15m", "30minutes": "30m",
    # Hours
    "h": "1h", "1hour": "1h", "2hour": "2h", "4hour": "4h",
    "1hours": "1h", "4hours": "4h",
    # Days
    "d": "1d", "daily": "1d", "day": "1d",
    "1day": "1d", "3day": "3d",
    # Weeks
    "w": "1w", "weekly": "1w", "week": "1w",
}


def parse_timeframe(raw: str):
    """Normalise a user-supplied timeframe string to a ccxt timeframe.

    Returns the ccxt string on success, or None if not supported.
    Examples: '1d', 'D', 'daily', '15min', '15m', '1H' → canonical form.
    """
    normalised = raw.strip().lower()
    # Direct match
    if normalised in VALID_TIMEFRAMES:
        return VALID_TIMEFRAMES[normalised]
    # Alias match
    if normalised in _TF_ALIASES:
        return VALID_TIMEFRAMES[_TF_ALIASES[normalised]]
    return None



# ─── Logging Setup ────────────────────────────────────────────────────

LOG_DIR = os.getenv("BOT_LOG_DIR", "./logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

formatter = logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def setup_logging():
    """Configure root logger with console + rotating file handlers."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Console (journald) — only add if not already present
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root_logger.addHandler(console)

    # Rotating file — ensure it's present exactly once
    if not any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)


# ─── Price Formatter ──────────────────────────────────────────────────

def fmt_price(price):
    """Adaptive price formatting for all price ranges."""
    if price == 0:
        return "$0"
    abs_price = abs(price)
    if abs_price >= 1.0:
        return f"${price:.2f}"
    elif abs_price >= 0.01:
        return f"${price:.4f}"
    elif abs_price >= 0.0001:
        return f"${price:.6f}"
    else:
        return f"${price:.8f}"
