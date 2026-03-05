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
        },
        "timeframe": "4h"
    }
    
    exchange = AsyncMock()
    exchange.fetch_ticker.return_value = {"last": 65000.0}

    with patch("telegram_handlers.save_state") as mock_save:
        await _handle_open(query, data, state, exchange)
        
        # Verify the position was added
        assert len(state["active_positions"]) == 1
        assert state["active_positions"][0]["symbol"] == "BTC/USDT"
        assert state["active_positions"][0]["entry_tf"] == "4h"
        
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

@pytest.mark.asyncio
async def test_clean_command():
    from telegram_handlers import clean
    update = AsyncMock()
    context = AsyncMock()
    state = {
        "sent_signals": {"A": "1", "B": "2"}
    }
    with patch("telegram_handlers.load_state", return_value=state):
        with patch("telegram_handlers.save_state") as mock_save:
            await clean(update, context)
            assert len(state["sent_signals"]) == 0
            mock_save.assert_called_once()
            update.message.reply_text.assert_called_once()

@pytest.mark.asyncio
async def test_close_position():
    from telegram_handlers import close_position
    update = AsyncMock()
    context = AsyncMock()
    context.args = ["SOL"]
    state = {
        "portfolio_balance": 25000.0,
        "available_cash": 20000.0,
        "tied_capital": 5000.0,
        "active_positions": [
            {
                "symbol": "SOL/USDT",
                "side": "LONG",
                "entry_price": 100.0,
                "allocated_capital": 5000.0,
                "tp1_hit": False,
            },
        ]
    }
    with patch("telegram_handlers.load_state", return_value=state),\
         patch("telegram_handlers.save_state") as mock_save,\
         patch("state_manager.log_trade") as mock_log,\
         patch("telegram_handlers.ccxt.binance") as mock_binance:

        exchange = AsyncMock()
        exchange.fetch_ticker.return_value = {"last": 110.0}  # 10% profit
        mock_binance.return_value = exchange

        await close_position(update, context)

        # Position removed
        assert len(state["active_positions"]) == 0
        # Capital released (+PnL)
        # net_pct = (110 - 100) / 100 * 100 - 0.2 = 9.8%
        # pnl = 5000 * 0.098 = 490
        assert abs(state["tied_capital"]) < 0.01  # should be 0
        assert abs(state["available_cash"] - (20000.0 + 5000.0 + 490.0)) < 1.0
        assert abs(state["portfolio_balance"] - (25000.0 + 490.0)) < 1.0
        mock_save.assert_called_once()
        mock_log.assert_called_once()
        update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_close_position_releases_capital_on_loss():
    """Verify that a losing close still correctly reduces tied_capital."""
    from telegram_handlers import close_position
    update = AsyncMock()
    context = AsyncMock()
    context.args = ["SOL"]
    state = {
        "portfolio_balance": 25000.0,
        "available_cash": 20000.0,
        "tied_capital": 5000.0,
        "active_positions": [
            {
                "symbol": "SOL/USDT",
                "side": "LONG",
                "entry_price": 100.0,
                "allocated_capital": 5000.0,
                "tp1_hit": False,
            },
        ]
    }
    with patch("telegram_handlers.load_state", return_value=state),\
         patch("telegram_handlers.save_state"),\
         patch("state_manager.log_trade"),\
         patch("telegram_handlers.ccxt.binance") as mock_binance:

        exchange = AsyncMock()
        exchange.fetch_ticker.return_value = {"last": 95.0}  # 5% loss
        mock_binance.return_value = exchange

        await close_position(update, context)

        assert len(state["active_positions"]) == 0
        # net_pct = (95 - 100) / 100 * 100 - 0.2 = -5.2%
        # pnl = 5000 * -0.052 = -260
        assert abs(state["tied_capital"]) < 0.01
        assert state["portfolio_balance"] < 25000.0  # balance decreased
        assert state["available_cash"] > 20000.0  # cash restored (minus loss)


@pytest.mark.asyncio
async def test_manual_long():
    from telegram_handlers import manual_long
    update = AsyncMock()
    context = AsyncMock()
    context.args = ["SOL"]
    state = {
        "portfolio_balance": 1000.0,
        "available_cash": 1000.0,
        "active_positions": [],
        "timeframe": "1d"
    }
    
    with patch("telegram_handlers.load_state", return_value=state),\
         patch("telegram_handlers.save_state") as mock_save,\
         patch("state_manager.log_trade"),\
         patch("telegram_handlers.ccxt.binance") as mock_binance:
         
        exchange = AsyncMock()
        exchange.fetch_ticker.return_value = {"last": 150.0}
        mock_binance.return_value = exchange
        
        # Test the fallback ATR logic if pandas fails or fetch_ohlcv fails
        await manual_long(update, context)
        assert len(state["active_positions"]) == 1
        pos = state["active_positions"][0]
        assert pos["symbol"] == "SOL/USDT"
        assert pos["side"] == "LONG"
        assert pos["entry_tf"] == "1d"

@pytest.mark.asyncio
async def test_update_sl():
    from telegram_handlers import update_sl
    update = AsyncMock()
    context = AsyncMock()
    context.args = ["SOL", "145.0"]
    state = {
        "active_positions": [
            {"symbol": "SOL/USDT", "side": "LONG", "current_sl": 140.0}
        ]
    }
    with patch("telegram_handlers.load_state", return_value=state),\
         patch("telegram_handlers.save_state") as mock_save:
         
        await update_sl(update, context)
        assert state["active_positions"][0]["current_sl"] == 145.0
        mock_save.assert_called_once()
        update.message.reply_text.assert_called_with("✅ Stop Loss for SOL/USDT updated to $145.00.")

@pytest.mark.asyncio
async def test_handle_sl_raised():
    from telegram_handlers import _handle_sl_raised
    query = AsyncMock()
    data = "slraised_SOL/USDT"
    state = {
        "active_positions": [
            {
                "symbol": "SOL/USDT", 
                "side": "LONG", 
                "entry_price": 100.0,
                "current_sl": 100.2, # BE SL
                "prev_tp_price": 110.0, # TP1
                "next_tp_price": 115.0, # TP2
                "next_tp_level": 3
            }
        ]
    }
    with patch("telegram_handlers.save_state") as mock_save:
        await _handle_sl_raised(query, data, state)
        pos = state["active_positions"][0]
        assert pos["current_sl"] == 110.0 # raised to prev TP
        assert pos["prev_tp_price"] == 110.0 # unchanged by button handler
        assert pos["next_tp_price"] == 115.0 # unchanged by button handler
        mock_save.assert_called_once()
        query.edit_message_text.assert_called_once()

@pytest.mark.asyncio
async def test_balance_command():
    from telegram_handlers import balance_command
    update = AsyncMock()
    context = AsyncMock()
    context.args = ["26000"]

    state = {
        "portfolio_balance": 25000.0,
        "available_cash": 15000.0,
        "tied_capital": 10000.0
    }

    with patch("telegram_handlers.load_state", return_value=state), \
         patch("telegram_handlers.save_state") as mock_save:
        await balance_command(update, context)

    # Balance increases by 1000, so available cash also increases by 1000
    assert state["portfolio_balance"] == 26000.0
    assert state["available_cash"] == 16000.0
    mock_save.assert_called_once()
    
    reply = update.message.reply_text.call_args[0][0]
    assert "25000.0" in reply
    assert "26000.0" in reply
