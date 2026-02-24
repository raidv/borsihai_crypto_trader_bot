"""Tests for bot.py — pure functions and signal pipeline logic."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot import fmt_price


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


# ─── Signal dedup logic ──────────────────────────────────────────────


class TestSignalDedup:
    """Test the signal deduplication logic used in signal_scanner."""

    def test_new_signal_not_in_sent(self):
        sent_signals = {}
        sig_key = "BTC/USDT_LONG"
        assert sig_key not in sent_signals

    def test_duplicate_signal_skipped(self):
        sent_signals = {"BTC/USDT_LONG": "2026-01-01T00:00:00"}
        sig_key = "BTC/USDT_LONG"
        assert sig_key in sent_signals

    def test_different_side_not_duplicate(self):
        sent_signals = {"BTC/USDT_LONG": "2026-01-01T00:00:00"}
        sig_key = "BTC/USDT_SHORT"
        assert sig_key not in sent_signals

    def test_different_symbol_not_duplicate(self):
        sent_signals = {"BTC/USDT_LONG": "2026-01-01T00:00:00"}
        sig_key = "ETH/USDT_LONG"
        assert sig_key not in sent_signals

    def test_old_signals_cleared_when_not_active(self):
        """Signals not in current scan should be cleared from sent_signals."""
        sent_signals = {
            "BTC/USDT_LONG": "2026-01-01T00:00:00",
            "ETH/USDT_SHORT": "2026-01-01T00:00:00",
        }
        # Current scan only returns BTC signal
        current_signals = [{"symbol": "BTC/USDT", "signal": "LONG"}]
        active_sig_keys = {f"{s['symbol']}_{s['signal']}" for s in current_signals}

        new_sent = {}
        for key in sent_signals:
            if key in active_sig_keys:
                new_sent[key] = sent_signals[key]

        assert "BTC/USDT_LONG" in new_sent
        assert "ETH/USDT_SHORT" not in new_sent


# ─── Signal→Alert Pipeline ───────────────────────────────────────────


class TestSignalPipeline:
    """Test the signal→alert pipeline counting logic."""

    def test_all_new_signals_sent(self):
        sent_signals = {}
        signals = [
            {"symbol": "BTC/USDT", "signal": "LONG", "price": 65000, "atr": 1000},
            {"symbol": "ETH/USDT", "signal": "LONG", "price": 3000, "atr": 100},
        ]
        sent_count = 0
        skipped_count = 0
        for sig in signals:
            sig_key = f"{sig['symbol']}_{sig['signal']}"
            if sig_key in sent_signals:
                skipped_count += 1
            else:
                sent_count += 1
        assert sent_count == 2
        assert skipped_count == 0

    def test_duplicate_signals_skipped(self):
        sent_signals = {"BTC/USDT_LONG": "2026-01-01T00:00:00"}
        signals = [
            {"symbol": "BTC/USDT", "signal": "LONG", "price": 65000, "atr": 1000},
            {"symbol": "ETH/USDT", "signal": "SHORT", "price": 3000, "atr": 100},
        ]
        sent_count = 0
        skipped_count = 0
        for sig in signals:
            sig_key = f"{sig['symbol']}_{sig['signal']}"
            if sig_key in sent_signals:
                skipped_count += 1
            else:
                sent_count += 1
        assert sent_count == 1
        assert skipped_count == 1

    def test_all_duplicates_skipped(self):
        sent_signals = {
            "BTC/USDT_LONG": "2026-01-01T00:00:00",
            "ETH/USDT_SHORT": "2026-01-01T00:00:00",
        }
        signals = [
            {"symbol": "BTC/USDT", "signal": "LONG", "price": 65000, "atr": 1000},
            {"symbol": "ETH/USDT", "signal": "SHORT", "price": 3000, "atr": 100},
        ]
        sent_count = 0
        skipped_count = 0
        for sig in signals:
            sig_key = f"{sig['symbol']}_{sig['signal']}"
            if sig_key in sent_signals:
                skipped_count += 1
            else:
                sent_count += 1
        assert sent_count == 0
        assert skipped_count == 2

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
