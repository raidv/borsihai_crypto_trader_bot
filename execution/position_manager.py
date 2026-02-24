"""Position monitoring — checks SL, TP1, and MACD exits every 5 minutes."""
import asyncio
import logging
from datetime import datetime, timezone

import ccxt.async_support as ccxt
import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import fmt_price
from state_manager import load_state, save_state

logger = logging.getLogger("Bot")


async def position_monitor(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 60s, acts every 5 minutes. Checks all open positions for SL/TP1/exit."""
    now = datetime.now(timezone.utc)

    # Run every 5 minutes (0, 5, 10, 15, ...)
    if now.minute % 5 != 0:
        return

    state = load_state()
    positions = state.get("active_positions", [])
    if not positions:
        return

    logger.info(f"Running 5-min position monitor at {now.strftime('%H:%M:%S')}")

    exchange = ccxt.binance()
    try:
        symbols = list(set(p['symbol'] for p in positions))

        # Fetch 5m candles + 1H MACD in parallel
        candle_results, macd_results = await _fetch_position_data(exchange, positions, symbols)

        candles_5m = {sym: c for sym, c in candle_results if c is not None}
        macd_dfs = {sym: df for sym, df in macd_results if df is not None}

        tickers = await exchange.fetch_tickers(symbols)

        for p in positions:
            await _check_position(context, p, tickers, candles_5m, macd_dfs)

        # Reset denial count for positions safely away from SL
        for p in positions:
            ticker = tickers.get(p['symbol'])
            if not ticker:
                continue
            current_price = ticker['last']
            sl = p['current_sl']
            side = p.get('side', 'LONG')
            breached = _check_sl_breach(side, current_price, sl, candles_5m.get(p['symbol']))
            if not breached:
                p['denial_count'] = 0

        save_state(state)
    finally:
        await exchange.close()


async def _fetch_position_data(exchange, positions, symbols):
    """Fetch 5m candles and 1H MACD data in parallel."""

    async def fetch_5m_candle(sym):
        try:
            ohlcv = await exchange.fetch_ohlcv(sym, "5m", limit=2)
            if ohlcv and len(ohlcv) >= 2:
                return sym, ohlcv[-2]
            return sym, None
        except Exception as e:
            logger.error(f"Error fetching 5m candle for {sym}: {e}")
            return sym, None

    async def fetch_1h_macd(sym):
        try:
            ohlcv = await exchange.fetch_ohlcv(sym, "1h", limit=50)
            if ohlcv and len(ohlcv) >= 30:
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df.ta.macd(fast=12, slow=26, signal=9, append=True)
                return sym, df
            return sym, None
        except Exception as e:
            logger.error(f"Error fetching 1H MACD for {sym}: {e}")
            return sym, None

    tp1_hit_symbols = list(set(p['symbol'] for p in positions if p.get('tp1_hit', False)))

    candle_tasks = [fetch_5m_candle(sym) for sym in symbols]
    macd_tasks = [fetch_1h_macd(sym) for sym in tp1_hit_symbols]

    all_results = await asyncio.gather(*candle_tasks, *macd_tasks)

    candle_results = all_results[:len(candle_tasks)]
    macd_results = all_results[len(candle_tasks):]

    return candle_results, macd_results


def _check_sl_breach(side, current_price, sl, candle_5m):
    """Check if SL has been breached via 5m wick or current price."""
    breached = False

    if candle_5m is not None:
        if side == "LONG" and candle_5m[3] <= sl:
            breached = True
        elif side == "SHORT" and candle_5m[2] >= sl:
            breached = True

    if not breached:
        if side == "LONG" and current_price <= sl:
            breached = True
        elif side == "SHORT" and current_price >= sl:
            breached = True

    return breached


async def _check_position(context, p, tickers, candles_5m, macd_dfs):
    """Check a single position for SL breach, TP1 hit, or MACD exit."""
    symbol = p['symbol']
    side = p.get('side', 'LONG')
    entry = p['entry_price']
    sl = p['current_sl']
    tp1 = p.get('tp1_price', 0)
    tp1_hit = p.get('tp1_hit', False)
    denial_count = p.get('denial_count', 0)

    ticker = tickers.get(symbol)
    if not ticker:
        return

    current_price = ticker['last']

    # ── Check SL breach ──
    breached = _check_sl_breach(side, current_price, sl, candles_5m.get(symbol))

    if breached:
        if denial_count < 2:
            keyboard = [
                [InlineKeyboardButton("✅ Closed", callback_data=f"slclosed_{symbol}"),
                 InlineKeyboardButton("❌ No, still open", callback_data=f"slopen_{symbol}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=context.job.chat_id,
                text=f"🚨 **ACTION REQUIRED: SL Breach** for {symbol} at {fmt_price(sl)}.\nDid it close automatically in StockTrak?",
                reply_markup=reply_markup
            )
        return

    # ── Check TP1 (only if not yet hit) ──
    if not tp1_hit and tp1 > 0:
        tp1_reached = _check_tp1(side, current_price, tp1, candles_5m.get(symbol))

        if tp1_reached:
            if side == "LONG":
                new_sl = entry * 1.002
            else:
                new_sl = entry * 0.998

            keyboard = [
                [InlineKeyboardButton("✅ Half-Closed", callback_data=f"halfclose_{symbol}"),
                 InlineKeyboardButton("❌ Ignore", callback_data=f"slopen_{symbol}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id=context.job.chat_id,
                text=(
                    f"⚡ **UPDATE: TP1 Hit** for {symbol}!\n"
                    f"Current price: {fmt_price(current_price)}\n"
                    f"Close 50% of your position now.\n"
                    f"Then raise your SL to {fmt_price(new_sl)} (break-even)."
                ),
                reply_markup=reply_markup
            )
            return

    # ── MACD momentum exit (only for positions where TP1 already hit) ──
    if tp1_hit and symbol in macd_dfs and denial_count < 2:
        await _check_macd_exit(context, p, current_price, macd_dfs[symbol])


def _check_tp1(side, current_price, tp1, candle_5m):
    """Check if TP1 has been reached via 5m wick or current price."""
    tp1_reached = False

    if candle_5m is not None:
        if side == "LONG" and candle_5m[2] >= tp1:
            tp1_reached = True
        elif side == "SHORT" and candle_5m[3] <= tp1:
            tp1_reached = True

    if not tp1_reached:
        if side == "LONG" and current_price >= tp1:
            tp1_reached = True
        elif side == "SHORT" and current_price <= tp1:
            tp1_reached = True

    return tp1_reached


async def _check_macd_exit(context, p, current_price, df):
    """Check for MACD momentum exit signal."""
    symbol = p['symbol']
    side = p.get('side', 'LONG')

    macd_line = df.get('MACD_12_26_9')
    macd_signal_line = df.get('MACDs_12_26_9')

    if macd_line is None or macd_signal_line is None:
        return

    ml_curr = macd_line.iloc[-2]
    ms_curr = macd_signal_line.iloc[-2]
    ml_prev = macd_line.iloc[-3]
    ms_prev = macd_signal_line.iloc[-3]

    if any(pd.isna(x) for x in [ml_curr, ms_curr, ml_prev, ms_prev]):
        return

    macd_exit = False
    reason = ""
    if side == "LONG" and ml_prev >= ms_prev and ml_curr < ms_curr:
        macd_exit = True
        reason = "MACD bearish cross on 1H"
    elif side == "SHORT" and ml_prev <= ms_prev and ml_curr > ms_curr:
        macd_exit = True
        reason = "MACD bullish cross on 1H"

    if macd_exit:
        keyboard = [
            [InlineKeyboardButton("✅ Closed", callback_data=f"slclosed_{symbol}"),
             InlineKeyboardButton("❌ Ignore", callback_data=f"slopen_{symbol}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=context.job.chat_id,
            text=(
                f"🚨 **ACTION REQUIRED: Momentum Exit** for {symbol}!\n"
                f"Reason: {reason}\n"
                f"Current price: {fmt_price(current_price)}\n"
                f"Close remaining position."
            ),
            reply_markup=reply_markup
        )
