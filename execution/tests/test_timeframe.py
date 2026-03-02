"""Tests for multi-timeframe support: parse_timeframe() and /timeframe command."""
import os
import sys
from unittest.mock import AsyncMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import parse_timeframe, TIMEFRAME_PAIRINGS, DEFAULT_TIMEFRAME


# ─── parse_timeframe tests ─────────────────────────────────────────────

class TestParseTimeframe:
    def test_exact_lowercase(self):
        assert parse_timeframe("1h") == "1h"
        assert parse_timeframe("4h") == "4h"
        assert parse_timeframe("1d") == "1d"
        assert parse_timeframe("15m") == "15m"

    def test_uppercase(self):
        assert parse_timeframe("1H") == "1h"
        assert parse_timeframe("4H") == "4h"
        assert parse_timeframe("1D") == "1d"
        assert parse_timeframe("15M") == "15m"

    def test_single_letter_aliases(self):
        assert parse_timeframe("h") == "1h"
        assert parse_timeframe("H") == "1h"
        assert parse_timeframe("d") == "1d"
        assert parse_timeframe("D") == "1d"
        assert parse_timeframe("w") == "1w"
        assert parse_timeframe("W") == "1w"

    def test_word_aliases(self):
        assert parse_timeframe("daily") == "1d"
        assert parse_timeframe("day") == "1d"
        assert parse_timeframe("weekly") == "1w"
        assert parse_timeframe("week") == "1w"

    def test_minute_aliases(self):
        assert parse_timeframe("15min") == "15m"
        assert parse_timeframe("15minutes") == "15m"
        assert parse_timeframe("5min") == "5m"
        assert parse_timeframe("30min") == "30m"
        assert parse_timeframe("1minute") == "1m"

    def test_hour_aliases(self):
        assert parse_timeframe("1hour") == "1h"
        assert parse_timeframe("4hour") == "4h"
        assert parse_timeframe("1hours") == "1h"

    def test_whitespace_stripped(self):
        assert parse_timeframe("  1h  ") == "1h"
        assert parse_timeframe(" 1D ") == "1d"

    def test_invalid_returns_none(self):
        assert parse_timeframe("3w") is None
        assert parse_timeframe("2w") is None
        assert parse_timeframe("invalid") is None
        assert parse_timeframe("hourly") is None
        assert parse_timeframe("") is None

    def test_all_valid_canonical_forms(self):
        """Every key in VALID_TIMEFRAMES resolves to itself."""
        from config import VALID_TIMEFRAMES
        for tf in VALID_TIMEFRAMES:
            assert parse_timeframe(tf) == tf, f"Failed for {tf}"


class TestTimeframePairings:
    def test_1h_uses_4h_trend(self):
        pairing = TIMEFRAME_PAIRINGS["1h"]
        assert pairing["trend"] == "4h"

    def test_1d_uses_1w_trend(self):
        pairing = TIMEFRAME_PAIRINGS["1d"]
        assert pairing["trend"] == "1w"

    def test_4h_uses_1d_trend(self):
        pairing = TIMEFRAME_PAIRINGS["4h"]
        assert pairing["trend"] == "1d"

    def test_all_pairings_have_required_keys(self):
        for tf, pairing in TIMEFRAME_PAIRINGS.items():
            assert "trend" in pairing, f"Missing 'trend' for {tf}"
            assert "monitor" in pairing, f"Missing 'monitor' for {tf}"
            assert "scan_interval" in pairing, f"Missing 'scan_interval' for {tf}"


# ─── /timeframe command tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeframe_command_valid():
    """Setting a valid timeframe updates state and notifies the user."""
    from telegram_handlers import timeframe_command
    update = AsyncMock()
    update.effective_chat.id = 12345
    context = AsyncMock()
    context.args = ["4h"]

    state = {
        "chat_id": 12345,
        "timeframe": "1h",
        "portfolio_balance": 25000.0,
        "available_cash": 25000.0,
    }

    with patch("telegram_handlers.load_state", return_value=state), \
         patch("telegram_handlers.save_state") as mock_save, \
         patch("bot.register_jobs") as mock_register:
        await timeframe_command(update, context)

    assert state["timeframe"] == "4h"
    mock_save.assert_called_once()
    mock_register.assert_called_once()
    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "4H" in reply
    assert "1H" in reply  # old TF shown


@pytest.mark.asyncio
async def test_timeframe_command_invalid():
    """An invalid timeframe shows an error and does NOT update state."""
    from telegram_handlers import timeframe_command
    update = AsyncMock()
    update.effective_chat.id = 12345
    context = AsyncMock()
    context.args = ["3w"]

    state = {"chat_id": 12345, "timeframe": "1h"}

    with patch("telegram_handlers.load_state", return_value=state), \
         patch("telegram_handlers.save_state") as mock_save, \
         patch("bot.register_jobs") as mock_register:
        await timeframe_command(update, context)

    assert state.get("timeframe") == "1h"  # unchanged
    mock_save.assert_not_called()
    mock_register.assert_not_called()
    reply = update.message.reply_text.call_args[0][0]
    assert "Unsupported" in reply or "unsupported" in reply.lower()


@pytest.mark.asyncio
async def test_timeframe_command_no_args():
    """Calling /timeframe without args shows current timeframe info."""
    from telegram_handlers import timeframe_command
    update = AsyncMock()
    update.effective_chat.id = 12345
    context = AsyncMock()
    context.args = []

    state = {"chat_id": 12345, "timeframe": "4h"}

    with patch("telegram_handlers.load_state", return_value=state), \
         patch("telegram_handlers.save_state"):
        await timeframe_command(update, context)

    reply = update.message.reply_text.call_args[0][0]
    assert "4H" in reply


@pytest.mark.asyncio
async def test_timeframe_command_case_insensitive():
    """1D, 1d, daily, D all set the timeframe to 1d."""
    from telegram_handlers import timeframe_command

    for alias in ["1D", "d", "D", "daily"]:
        update = AsyncMock()
        update.effective_chat.id = 12345
        context = AsyncMock()
        context.args = [alias]
        state = {"chat_id": 12345, "timeframe": "1h"}

        with patch("telegram_handlers.load_state", return_value=state), \
             patch("telegram_handlers.save_state"), \
             patch("bot.register_jobs"):
            await timeframe_command(update, context)

        assert state["timeframe"] == "1d", f"Expected 1d for alias '{alias}', got {state.get('timeframe')}"
