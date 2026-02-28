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
