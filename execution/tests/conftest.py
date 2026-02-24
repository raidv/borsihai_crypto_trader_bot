"""Shared test fixtures for Börsihai crypto trader tests."""
import os
import sys
import json
import pytest
import numpy as np
import pandas as pd

# Add execution/ to sys.path so we can import modules as the bot does
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def tmp_state_file(tmp_path):
    """Provide a temporary state file path and patch STATE_FILE."""
    state_file = str(tmp_path / "state.json")
    return state_file


@pytest.fixture
def tmp_trade_log(tmp_path):
    """Provide a temporary trade log file path."""
    trade_log = str(tmp_path / "trade_log.json")
    return trade_log


def make_ohlcv_df(n=210, base_price=100.0, trend="flat", volatility=0.02):
    """Create a synthetic OHLCV DataFrame for testing.

    Args:
        n: Number of candles.
        base_price: Starting price.
        trend: 'up', 'down', or 'flat'.
        volatility: Random price variation factor.
    """
    np.random.seed(42)
    timestamps = pd.date_range("2026-01-01", periods=n, freq="1h")
    prices = np.zeros(n)
    prices[0] = base_price

    if trend == "up":
        drift = 0.002
    elif trend == "down":
        drift = -0.002
    else:
        drift = 0.0

    for i in range(1, n):
        prices[i] = prices[i - 1] * (1 + drift + np.random.randn() * volatility)

    highs = prices * (1 + np.random.rand(n) * volatility)
    lows = prices * (1 - np.random.rand(n) * volatility)
    opens = prices * (1 + (np.random.rand(n) - 0.5) * volatility)
    volumes = np.random.rand(n) * 1000000 + 500000

    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": prices,
            "volume": volumes,
        }
    )
    return df


def make_ohlcv_with_long_signal(n=100):
    """Create OHLCV data that will produce a LONG entry signal.

    Conditions for LONG:
    - EMA 20 > EMA 50 (price trending up)
    - MACD line crosses above signal line (prev: MACD <= Signal, curr: MACD > Signal)
    - MACD histogram > 0
    """
    np.random.seed(123)

    # Start with a downtrend then switch to uptrend to get the cross
    prices = np.zeros(n)
    prices[0] = 100.0

    # Slightly down for first half, then strong up
    for i in range(1, n):
        if i < n // 2:
            prices[i] = prices[i - 1] * (1 - 0.001 + np.random.randn() * 0.005)
        else:
            prices[i] = prices[i - 1] * (1 + 0.005 + np.random.randn() * 0.003)

    timestamps = pd.date_range("2026-01-01", periods=n, freq="1h")
    highs = prices * 1.005
    lows = prices * 0.995
    opens = prices * (1 + (np.random.rand(n) - 0.5) * 0.003)
    volumes = np.random.rand(n) * 1000000 + 500000

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": prices,
            "volume": volumes,
        }
    )


def make_ohlcv_with_short_signal(n=100):
    """Create OHLCV data that will produce a SHORT entry signal.

    Conditions for SHORT:
    - EMA 20 < EMA 50 (price trending down)
    - MACD line crosses below signal line
    - MACD histogram < 0
    """
    np.random.seed(456)
    prices = np.zeros(n)
    prices[0] = 100.0

    # Up for first half, then strong down
    for i in range(1, n):
        if i < n // 2:
            prices[i] = prices[i - 1] * (1 + 0.001 + np.random.randn() * 0.005)
        else:
            prices[i] = prices[i - 1] * (1 - 0.005 + np.random.randn() * 0.003)

    timestamps = pd.date_range("2026-01-01", periods=n, freq="1h")
    highs = prices * 1.005
    lows = prices * 0.995
    opens = prices * (1 + (np.random.rand(n) - 0.5) * 0.003)
    volumes = np.random.rand(n) * 1000000 + 500000

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": prices,
            "volume": volumes,
        }
    )


def make_ohlcv_raw(data, base_ts=1704067200000):
    """Convert list of [open, high, low, close, volume] to raw OHLCV format
    as returned by ccxt (list of lists with timestamp first)."""
    result = []
    for i, row in enumerate(data):
        ts = base_ts + i * 3600000  # 1h intervals
        result.append([ts, row[0], row[1], row[2], row[3], row[4]])
    return result
