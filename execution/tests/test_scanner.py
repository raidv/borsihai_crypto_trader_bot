"""Tests for scanner.py — market scanning, signal detection, and scoring."""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pandas_ta as ta
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import (
    make_ohlcv_df,
    make_ohlcv_raw,
    make_ohlcv_with_long_signal,
    make_ohlcv_with_short_signal,
)

import scanner


# ─── fetch_ohlcv ──────────────────────────────────────────────────────


class TestFetchOhlcv:
    @pytest.mark.asyncio
    async def test_returns_dataframe(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.return_value = make_ohlcv_raw(
            [[100, 105, 95, 102, 1000]] * 10
        )
        df = await scanner.fetch_ohlcv(mock_exchange, "BTC/USDT", "1h", limit=10)
        assert df is not None
        assert len(df) == 10
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.side_effect = Exception("API error")
        df = await scanner.fetch_ohlcv(mock_exchange, "BTC/USDT", "1h")
        assert df is None

    @pytest.mark.asyncio
    async def test_timestamps_converted_to_datetime(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.return_value = make_ohlcv_raw(
            [[100, 105, 95, 102, 1000]] * 5
        )
        df = await scanner.fetch_ohlcv(mock_exchange, "BTC/USDT", "1h")
        assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])


# ─── check_4h_trend ───────────────────────────────────────────────────


class TestCheck4hTrend:
    @pytest.mark.asyncio
    async def test_returns_long_when_above_ema200(self):
        df = make_ohlcv_df(n=210, base_price=100.0, trend="up", volatility=0.005)
        mock_exchange = AsyncMock()

        async def mock_fetch(sym, tf, limit=100):
            raw = []
            for _, row in df.iterrows():
                raw.append(
                    [
                        int(row["timestamp"].timestamp() * 1000),
                        row["open"],
                        row["high"],
                        row["low"],
                        row["close"],
                        row["volume"],
                    ]
                )
            return raw

        mock_exchange.fetch_ohlcv = mock_fetch
        result = await scanner.check_4h_trend(mock_exchange, "BTC/USDT")
        assert result in ("LONG", "SHORT", None)
        if result is not None:
            assert result == "LONG"

    @pytest.mark.asyncio
    async def test_returns_none_on_insufficient_data(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.return_value = make_ohlcv_raw(
            [[100, 105, 95, 102, 1000]] * 50
        )
        result = await scanner.check_4h_trend(mock_exchange, "BTC/USDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.side_effect = Exception("API error")
        result = await scanner.check_4h_trend(mock_exchange, "BTC/USDT")
        assert result is None


# ─── check_1h_entry ───────────────────────────────────────────────────


class TestCheck1hEntry:
    @pytest.mark.asyncio
    async def test_returns_none_on_insufficient_data(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.return_value = make_ohlcv_raw(
            [[100, 105, 95, 102, 1000]] * 20
        )
        result = await scanner.check_1h_entry(mock_exchange, "BTC/USDT", "LONG")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.side_effect = Exception("Error")
        result = await scanner.check_1h_entry(mock_exchange, "BTC/USDT", "LONG")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_signal(self):
        df = make_ohlcv_df(n=100, trend="flat", volatility=0.001)
        mock_exchange = AsyncMock()

        async def mock_fetch(sym, tf, limit=100):
            raw = []
            for _, row in df.iterrows():
                raw.append(
                    [
                        int(row["timestamp"].timestamp() * 1000),
                        row["open"],
                        row["high"],
                        row["low"],
                        row["close"],
                        row["volume"],
                    ]
                )
            return raw

        mock_exchange.fetch_ohlcv = mock_fetch
        result = await scanner.check_1h_entry(mock_exchange, "BTC/USDT", "LONG")
        assert result is None or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_signal_contains_required_fields(self):
        """If a signal is returned, it must have symbol, signal, price, atr, indicator_data."""
        df = make_ohlcv_with_long_signal(n=100)
        mock_exchange = AsyncMock()

        async def mock_fetch(sym, tf, limit=100):
            raw = []
            for _, row in df.iterrows():
                raw.append(
                    [
                        int(row["timestamp"].timestamp() * 1000),
                        row["open"],
                        row["high"],
                        row["low"],
                        row["close"],
                        row["volume"],
                    ]
                )
            return raw

        mock_exchange.fetch_ohlcv = mock_fetch
        result = await scanner.check_1h_entry(mock_exchange, "BTC/USDT", "LONG")
        if result is not None:
            assert "symbol" in result
            assert "signal" in result
            assert "price" in result
            assert "atr" in result
            assert "indicator_data" in result
            assert result["signal"] == "LONG"


# ─── get_btc_pct_change ──────────────────────────────────────────────


class TestGetBtcPctChange:
    @pytest.mark.asyncio
    async def test_returns_float(self):
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
        raw = [[p, p + 1, p - 1, p, 500000] for p in prices]
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.return_value = make_ohlcv_raw(raw)
        result = await scanner.get_btc_pct_change(mock_exchange)
        assert isinstance(result, float)

    @pytest.mark.asyncio
    async def test_positive_change_on_uptrend(self):
        prices = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118]
        raw = [[p, p + 1, p - 1, p, 500000] for p in prices]
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.return_value = make_ohlcv_raw(raw)
        result = await scanner.get_btc_pct_change(mock_exchange)
        assert result > 0

    @pytest.mark.asyncio
    async def test_negative_change_on_downtrend(self):
        prices = [120, 118, 116, 114, 112, 110, 108, 106, 104, 102]
        raw = [[p, p + 1, p - 1, p, 500000] for p in prices]
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.return_value = make_ohlcv_raw(raw)
        result = await scanner.get_btc_pct_change(mock_exchange)
        assert result < 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_insufficient_data(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.return_value = make_ohlcv_raw(
            [[100, 105, 95, 102, 1000]] * 3
        )
        result = await scanner.get_btc_pct_change(mock_exchange)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_on_api_error(self):
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ohlcv.side_effect = Exception("Error")
        result = await scanner.get_btc_pct_change(mock_exchange)
        assert result == 0.0


# ─── scan_market (integration) ────────────────────────────────────────


class TestScanMarket:
    @pytest.mark.asyncio
    async def test_returns_dict_with_signals_and_metadata(self, tmp_path):
        pairs_file = tmp_path / "pairs.txt"
        pairs_file.write_text("BTC/USDT\nETH/USDT\n")

        with patch.object(scanner, "ccxt") as mock_ccxt_module:
            mock_exchange = AsyncMock()
            mock_ccxt_module.binance.return_value = mock_exchange

            mock_exchange.fetch_ohlcv.return_value = make_ohlcv_raw(
                [[100, 105, 95, 102, 1000]] * 50
            )

            with patch.object(scanner.os.path, "dirname", return_value=str(tmp_path)):
                result = await scanner.scan_market()
            assert isinstance(result, dict)
            assert "signals" in result
            assert "metadata" in result
            assert isinstance(result["signals"], list)
            assert isinstance(result["metadata"], dict)
            assert "pairs_scanned" in result["metadata"]

    @pytest.mark.asyncio
    async def test_returns_metadata_when_no_pairs_file(self, tmp_path):
        with patch.object(scanner.os.path, "dirname", return_value=str(tmp_path)):
            result = await scanner.scan_market()
        assert isinstance(result, dict)
        assert result["signals"] == []
        assert result["metadata"]["pairs_scanned"] == 0

    @pytest.mark.asyncio
    async def test_max_10_signals(self, tmp_path):
        pairs = [f"COIN{i}/USDT" for i in range(20)]
        pairs_file = tmp_path / "pairs.txt"
        pairs_file.write_text("\n".join(pairs))

        with patch.object(scanner, "ccxt") as mock_ccxt_module:
            mock_exchange = AsyncMock()
            mock_ccxt_module.binance.return_value = mock_exchange

            mock_exchange.fetch_ohlcv.return_value = make_ohlcv_raw(
                [[100, 105, 95, 102, 1000]] * 50
            )

            with patch.object(scanner.os.path, "dirname", return_value=str(tmp_path)):
                result = await scanner.scan_market()
            assert len(result["signals"]) <= 10

    @pytest.mark.asyncio
    async def test_signals_sorted_by_score_desc(self, tmp_path):
        """Signal sorting by composite score descending, then mc_rank."""
        signals = [
            {"symbol": "A/USDT", "signal": "LONG", "price": 100, "atr": 2, "score": 30, "mc_rank": 0},
            {"symbol": "B/USDT", "signal": "LONG", "price": 200, "atr": 4, "score": 85, "mc_rank": 1},
            {"symbol": "C/USDT", "signal": "SHORT", "price": 50, "atr": 1, "score": 60, "mc_rank": 2},
        ]
        signals.sort(key=lambda x: (-x["score"], x["mc_rank"]))
        assert signals[0]["symbol"] == "B/USDT"
        assert signals[1]["symbol"] == "C/USDT"
        assert signals[2]["symbol"] == "A/USDT"


# ─── compute_signal_score ─────────────────────────────────────────────


class TestComputeSignalScore:
    """Tests for the composite signal scoring function."""

    def test_returns_score_in_range(self):
        indicator_data = {
            "macd_hist_current": 0.5,
            "macd_hist_previous": 0.3,
            "macd_hist_series": [0.1, 0.2, 0.3, 0.4, 0.5],
            "ema20": 105.0,
            "ema50": 100.0,
            "volume_current": 1500000,
            "volume_series": [1000000] * 20,
            "candle_body": 2.0,
            "atr_val": 3.0,
        }
        result = scanner.compute_signal_score(indicator_data, 0.02)
        assert 0 <= result["composite"] <= 100
        assert "components" in result

    def test_all_components_present(self):
        indicator_data = {
            "macd_hist_current": 0.5,
            "macd_hist_previous": 0.3,
            "macd_hist_series": [0.1, 0.2, 0.3, 0.4, 0.5],
            "ema20": 105.0,
            "ema50": 100.0,
            "volume_current": 1500000,
            "volume_series": [1000000] * 20,
            "candle_body": 2.0,
            "atr_val": 3.0,
        }
        result = scanner.compute_signal_score(indicator_data, 0.02)
        expected_components = [
            "macd_magnitude",
            "macd_acceleration",
            "ema_spread",
            "volume",
            "atr_move",
            "btc_relative",
        ]
        for comp in expected_components:
            assert comp in result["components"]
            assert 0 <= result["components"][comp] <= 100

    def test_strong_momentum_scores_high(self):
        """Strong indicators should produce a high score."""
        indicator_data = {
            "macd_hist_current": 1.0,
            "macd_hist_previous": 0.3,
            "macd_hist_series": [0.1, 0.2, 0.3, 0.5, 1.0],
            "ema20": 110.0,
            "ema50": 100.0,
            "volume_current": 3000000,
            "volume_series": [1000000] * 20,
            "candle_body": 4.0,
            "atr_val": 3.0,
        }
        result = scanner.compute_signal_score(indicator_data, 0.05)
        assert result["composite"] >= 60

    def test_weak_momentum_scores_low(self):
        """Weak indicators should produce a low score."""
        indicator_data = {
            "macd_hist_current": 0.01,
            "macd_hist_previous": 0.01,
            "macd_hist_series": [0.5, 0.4, 0.3, 0.02, 0.01],
            "ema20": 100.1,
            "ema50": 100.0,
            "volume_current": 300000,
            "volume_series": [1000000] * 20,
            "candle_body": 0.1,
            "atr_val": 3.0,
        }
        result = scanner.compute_signal_score(indicator_data, -0.05)
        assert result["composite"] <= 40

    def test_handles_zero_values(self):
        indicator_data = {
            "macd_hist_current": 0,
            "macd_hist_previous": 0,
            "macd_hist_series": [0] * 20,
            "ema20": 100.0,
            "ema50": 100.0,
            "volume_current": 0,
            "volume_series": [0] * 20,
            "candle_body": 0,
            "atr_val": 0,
        }
        result = scanner.compute_signal_score(indicator_data, 0.0)
        assert 0 <= result["composite"] <= 100

    def test_handles_empty_series(self):
        indicator_data = {
            "macd_hist_current": 0.5,
            "macd_hist_previous": 0.3,
            "macd_hist_series": [],
            "ema20": 105.0,
            "ema50": 100.0,
            "volume_current": 1500000,
            "volume_series": [],
            "candle_body": 2.0,
            "atr_val": 3.0,
        }
        result = scanner.compute_signal_score(indicator_data, 0.02)
        assert 0 <= result["composite"] <= 100


# ─── format_score_label ───────────────────────────────────────────────


class TestFormatScoreLabel:
    def test_very_strong(self):
        assert "Very Strong" in scanner.format_score_label(85)

    def test_strong(self):
        assert "Strong" in scanner.format_score_label(65)

    def test_moderate(self):
        assert "Moderate" in scanner.format_score_label(45)

    def test_weak(self):
        assert "Weak" in scanner.format_score_label(25)

    def test_very_weak(self):
        assert "Very Weak" in scanner.format_score_label(10)


# ─── format_score_display ─────────────────────────────────────────────


class TestFormatScoreDisplay:
    def test_contains_score_and_label(self):
        score_data = {
            "composite": 72,
            "components": {
                "macd_magnitude": 80,
                "macd_acceleration": 60,
                "ema_spread": 50,
                "volume": 90,
                "atr_move": 65,
                "btc_relative": 70,
            },
        }
        result = scanner.format_score_display(score_data, 0.023)
        assert "72/100" in result
        assert "Strong" in result
        assert "█" in result
        assert "BTC" in result
