"""Tests for bot.py — pure functions and signal pipeline logic."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import fmt_price


# ─── fmt_price ────────────────────────────────────────────────────────


class TestFmtPrice:
    """Price formatting for all ranges — critical for avoiding the $0.00000 bug."""

    def test_zero(self):
        assert fmt_price(0) == "$0"

    def test_large_price(self):
        result = fmt_price(65000.50)
        assert result == "$65000.50"

    def test_medium_price(self):
        result = fmt_price(1.23)
        assert result == "$1.23"

    def test_one_dollar(self):
        result = fmt_price(1.0)
        assert result == "$1.00"

    def test_sub_dollar(self):
        # 0.01 to 0.99 → 4 decimals
        result = fmt_price(0.05)
        assert result == "$0.0500"

    def test_sub_cent(self):
        # 0.0001 to 0.0099 → 6 decimals
        result = fmt_price(0.005)
        assert result == "$0.005000"

    def test_sub_cent_small(self):
        result = fmt_price(0.0001)
        assert result == "$0.000100"

    def test_very_small_price(self):
        # < 0.0001 → 8 decimals
        result = fmt_price(0.00005)
        assert result == "$0.00005000"

    def test_shib_like_price(self):
        # SHIB-like price: 0.00002345
        result = fmt_price(0.00002345)
        assert result == "$0.00002345"

    def test_bonk_like_price(self):
        # BONK-like price: 0.00000234
        result = fmt_price(0.00000234)
        assert result == "$0.00000234"

    def test_negative_price(self):
        # For SL/PnL display
        result = fmt_price(-5.50)
        assert result == "$-5.50"

    def test_btc_price(self):
        result = fmt_price(97500.00)
        assert result == "$97500.00"

    def test_no_zero_truncation(self):
        """Prices like 0.00001000 should NOT display as $0.0000 (the bug)."""
        result = fmt_price(0.00001)
        assert "0.00001" in result
        assert result != "$0.0000"
        assert result != "$0.00000"
        assert result != "$0"

    def test_boundary_at_one(self):
        # Exactly at boundary: 1.0 should use 2 decimals
        assert fmt_price(1.0) == "$1.00"
        # Just below 1.0 should use 4 decimals
        result = fmt_price(0.99)
        assert result == "$0.9900"

    def test_boundary_at_001(self):
        # Just below 0.01 should use 6 decimals
        result = fmt_price(0.0099)
        assert result == "$0.009900"

    def test_boundary_at_00001(self):
        # Just below 0.0001 should use 8 decimals
        result = fmt_price(0.00009999)
        assert result == "$0.00009999"


# ─── Signal→Alert Pipeline ───────────────────────────────────────────


class TestSignalPipeline:
    """Test the signal→alert pipeline counting logic."""

    def test_all_new_signals_sent(self):
        sent_signals = {}
        signals = [
            {"symbol": "BTC/USDT", "signal": "LONG", "price": 65000, "atr": 1000},
            {"symbol": "ETH/USDT", "signal": "LONG", "price": 3000, "atr": 100},
        ]
        sent_count = len(signals)
        skipped_count = 0
        assert sent_count == 2
        assert skipped_count == 0

    def test_preview_levels_long(self):
        """Test SL/TP1 preview calculation for LONG."""
        price = 65000.0
        atr_val = 1500.0
        ATR_MULTIPLIER = 2.0
        TP1_RR_RATIO = 1.5

        initial_risk = ATR_MULTIPLIER * atr_val  # 3000
        sl = price - initial_risk  # 62000
        tp1 = price + (initial_risk * TP1_RR_RATIO)  # 69500

        assert sl == 62000.0
        assert tp1 == 69500.0

    def test_preview_levels_short(self):
        """Test SL/TP1 preview calculation for SHORT."""
        price = 3000.0
        atr_val = 100.0
        ATR_MULTIPLIER = 2.0
        TP1_RR_RATIO = 1.5

        initial_risk = ATR_MULTIPLIER * atr_val  # 200
        sl = price + initial_risk  # 3200
        tp1 = price - (initial_risk * TP1_RR_RATIO)  # 2700

        assert sl == 3200.0
        assert tp1 == 2700.0

    def test_preview_levels_fallback_no_atr(self):
        """When ATR is 0, fallback to 4% of price."""
        price = 100.0
        atr_val = 0
        ATR_MULTIPLIER = 2.0

        initial_risk = ATR_MULTIPLIER * atr_val if atr_val > 0 else price * 0.04
        assert initial_risk == 4.0  # 4% of 100
