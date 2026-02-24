"""Tests for state_manager.py — state persistence and trade logging."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import state_manager


class TestLoadState:
    """Tests for load_state()."""

    def test_creates_default_state_when_missing(self, tmp_state_file, monkeypatch):
        monkeypatch.setattr(state_manager, "STATE_FILE", tmp_state_file)
        state = state_manager.load_state()

        assert state["portfolio_balance"] == 25000.0
        assert state["available_cash"] == 25000.0
        assert state["tied_capital"] == 0.0
        assert state["bot_status"] == "ready"
        assert state["active_positions"] == []
        # Should also have written the file
        assert os.path.exists(tmp_state_file)

    def test_loads_existing_state(self, tmp_state_file, monkeypatch):
        monkeypatch.setattr(state_manager, "STATE_FILE", tmp_state_file)
        expected = {
            "portfolio_balance": 30000.0,
            "available_cash": 20000.0,
            "tied_capital": 10000.0,
            "bot_status": "afk",
            "active_positions": [{"symbol": "BTC/USDT", "side": "LONG"}],
            "chat_id": 12345,
        }
        with open(tmp_state_file, "w") as f:
            json.dump(expected, f)

        state = state_manager.load_state()
        assert state == expected

    def test_roundtrip(self, tmp_state_file, monkeypatch):
        monkeypatch.setattr(state_manager, "STATE_FILE", tmp_state_file)
        original = {
            "portfolio_balance": 42000.0,
            "available_cash": 32000.0,
            "tied_capital": 10000.0,
            "bot_status": "ready",
            "active_positions": [
                {"symbol": "ETH/USDT", "side": "SHORT", "entry_price": 3000.0}
            ],
            "chat_id": 99999,
            "sent_signals": {"BTC/USDT_LONG": "2026-01-01T00:00:00"},
        }
        state_manager.save_state(original)
        loaded = state_manager.load_state()
        assert loaded == original


class TestSaveState:
    """Tests for save_state() — atomic write behavior."""

    def test_atomic_write_no_tmp_leftover(self, tmp_state_file, monkeypatch):
        monkeypatch.setattr(state_manager, "STATE_FILE", tmp_state_file)
        state_manager.save_state({"test": True})
        # .tmp file should not remain
        assert not os.path.exists(tmp_state_file + ".tmp")
        assert os.path.exists(tmp_state_file)

    def test_overwrites_existing(self, tmp_state_file, monkeypatch):
        monkeypatch.setattr(state_manager, "STATE_FILE", tmp_state_file)
        state_manager.save_state({"version": 1})
        state_manager.save_state({"version": 2})
        with open(tmp_state_file, "r") as f:
            data = json.load(f)
        assert data["version"] == 2


class TestLogTrade:
    """Tests for log_trade()."""

    def test_creates_new_log(self, tmp_trade_log, monkeypatch):
        monkeypatch.setattr(state_manager, "TRADE_LOG_FILE", tmp_trade_log)
        state_manager.log_trade(
            "OPEN", "BTC/USDT", "LONG", 65000.0, 63000.0, 1704067200.0
        )
        with open(tmp_trade_log, "r") as f:
            log = json.load(f)
        assert len(log) == 1
        assert log[0]["action"] == "OPEN"
        assert log[0]["symbol"] == "BTC/USDT"
        assert log[0]["side"] == "LONG"
        assert log[0]["price"] == 65000.0
        assert log[0]["sl"] == 63000.0
        assert log[0]["pnl"] is None

    def test_appends_to_existing_log(self, tmp_trade_log, monkeypatch):
        monkeypatch.setattr(state_manager, "TRADE_LOG_FILE", tmp_trade_log)
        state_manager.log_trade(
            "OPEN", "BTC/USDT", "LONG", 65000.0, 63000.0, 1704067200.0
        )
        state_manager.log_trade(
            "CLOSE", "BTC/USDT", "LONG", 65000.0, 67000.0, 1704070800.0, pnl=500.0
        )
        with open(tmp_trade_log, "r") as f:
            log = json.load(f)
        assert len(log) == 2
        assert log[1]["action"] == "CLOSE"
        assert log[1]["pnl"] == 500.0

    def test_handles_corrupt_log_file(self, tmp_trade_log, monkeypatch):
        monkeypatch.setattr(state_manager, "TRADE_LOG_FILE", tmp_trade_log)
        # Write corrupt data
        with open(tmp_trade_log, "w") as f:
            f.write("not json{{{")
        # Should not raise, should start fresh
        state_manager.log_trade(
            "OPEN", "ETH/USDT", "SHORT", 3000.0, 3100.0, 1704067200.0
        )
        with open(tmp_trade_log, "r") as f:
            log = json.load(f)
        assert len(log) == 1

    def test_pnl_recorded_on_close(self, tmp_trade_log, monkeypatch):
        monkeypatch.setattr(state_manager, "TRADE_LOG_FILE", tmp_trade_log)
        state_manager.log_trade(
            "PARTIAL_CLOSE",
            "SOL/USDT",
            "LONG",
            150.0,
            165.0,
            1704067200.0,
            pnl=250.0,
        )
        with open(tmp_trade_log, "r") as f:
            log = json.load(f)
        assert log[0]["pnl"] == 250.0

    def test_timestamp_conversion(self, tmp_trade_log, monkeypatch):
        monkeypatch.setattr(state_manager, "TRADE_LOG_FILE", tmp_trade_log)
        state_manager.log_trade(
            "OPEN", "BTC/USDT", "LONG", 65000.0, 63000.0, 1704067200.0
        )
        with open(tmp_trade_log, "r") as f:
            log = json.load(f)
        # Timestamp should be ISO format string
        assert "2024-01-01" in log[0]["timestamp"]
