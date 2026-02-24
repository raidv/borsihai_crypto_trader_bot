"""Tests for telegram_handlers.py — interaction and state cleanup."""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegram_handlers import _handle_open, button_handler

@pytest.mark.asyncio
async def test_handle_open_clears_sent_signal():
    """Test that opening a position clears the sent signal to prevent future blockage."""
    query = AsyncMock()
    data = "open_LONG_BTC/USDT_1000.0_TA"
    
    state = {
        "portfolio_balance": 10000.0,
        "available_cash": 10000.0,
        "active_positions": [],
        "sent_signals": {
            "BTC/USDT_LONG": "2026-01-01T00:00:00",
            "ETH/USDT_SHORT": "2026-01-01T00:00:00"
        }
    }
    
    exchange = AsyncMock()
    exchange.fetch_ticker.return_value = {"last": 65000.0}

    with patch("telegram_handlers.save_state") as mock_save:
        await _handle_open(query, data, state, exchange)
        
        # Verify the position was added
        assert len(state["active_positions"]) == 1
        assert state["active_positions"][0]["symbol"] == "BTC/USDT"
        
        # Verify the sent signal was cleared
        assert "BTC/USDT_LONG" not in state["sent_signals"]
        assert "ETH/USDT_SHORT" in state["sent_signals"]
        
        # Verify state was saved
        mock_save.assert_called_once_with(state)


@pytest.mark.asyncio
async def test_ignore_button_clears_sent_signal():
    """Test that ignoring a signal clears it from sent_signals."""
    update = AsyncMock()
    query = AsyncMock()
    query.data = "ignore_ETH/USDT_SHORT"
    update.callback_query = query
    
    context = AsyncMock()
    
    state = {
        "sent_signals": {
            "BTC/USDT_LONG": "2026-01-01T00:00:00",
            "ETH/USDT_SHORT": "2026-01-01T00:00:00"
        }
    }
    
    with patch("telegram_handlers.load_state", return_value=state):
        with patch("telegram_handlers.save_state") as mock_save:
            with patch("telegram_handlers.ccxt.binance", return_value=AsyncMock()):
                await button_handler(update, context)
                
                # Verify the sent signal was cleared
                assert "ETH/USDT_SHORT" not in state["sent_signals"]
                assert "BTC/USDT_LONG" in state["sent_signals"]
                
                # Verify state was saved
                mock_save.assert_called_once_with(state)
                
                # Verify message was updated
                query.edit_message_text.assert_called_once_with("❌ Ignored signal.")
