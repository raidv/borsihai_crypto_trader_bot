"""Telegram command and button handlers for Börsihai."""
import logging
import math
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import ccxt.async_support as ccxt

from config import (
    MAX_POSITIONS, POSITION_SIZE_PCT, ATR_MULTIPLIER, TP1_RR_RATIO,
    DEFAULT_PORTFOLIO_BALANCE, fmt_price,
)
from state_manager import load_state, save_state, log_trade

logger = logging.getLogger("Bot")


# ─── COMMAND HANDLERS ──────────────────────────────────────────────────

def _ensure_chat_id(update, state):
    """Auto-capture chat_id from any incoming command if not stored.
    Returns (state, was_newly_registered)."""
    chat_id = update.effective_chat.id
    if not state.get("chat_id"):
        state["chat_id"] = chat_id
        save_state(state)
        return state, True
    return state, False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot import register_jobs
    chat_id = update.effective_chat.id
    state = load_state()
    state["chat_id"] = chat_id
    save_state(state)

    register_jobs(context, chat_id)
    await update.message.reply_text("🦈 Börsihai 2026 Swing Assistant is online. Monitoring 1H/4H strategy.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state, newly_registered = _ensure_chat_id(update, state)

    balance = state.get("portfolio_balance", 0.0)
    available_cash = state.get("available_cash", balance)
    tied_capital = state.get("tied_capital", 0.0)
    positions = state.get("active_positions", [])

    msg = f"📊 **Portfolio Status**\n"
    msg += f"Total Equity: ${balance:.2f}\n"
    msg += f"Available Cash: ${available_cash:.2f}\n"
    msg += f"Tied in Assets: ${tied_capital:.2f}\n"
    msg += f"Open Positions: {len(positions)}/{MAX_POSITIONS}\n"

    if not positions:
        msg += "\nNo active positions."
    else:
        msg += "\n**Active Positions:**\n"
        for p in positions:
            tp1_status = "✅ Hit" if p.get('tp1_hit', False) else "⏳ Pending"
            msg += f"- {p['symbol']} ({p.get('side', 'LONG')})\n"
            msg += f"  Entry: {fmt_price(p['entry_price'])} | SL: {fmt_price(p['current_sl'])}\n"
            msg += f"  TP1: {fmt_price(p.get('tp1_price', 0))} [{tp1_status}]\n"

    if newly_registered:
        msg += "\n\nℹ️ Chat ID registered. Send /start to activate monitoring jobs."

    await update.message.reply_text(msg)


async def afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state, newly_registered = _ensure_chat_id(update, state)
    state["bot_status"] = "afk"

    positions = state.get("active_positions", [])
    if not positions:
        save_state(state)
        msg = "😴 Bot is now AFK. No incoming signals."
        if newly_registered:
            msg += "\n\nℹ️ Chat ID registered. Send /start to activate monitoring jobs."
        await update.message.reply_text(msg)
        return

    exchange = ccxt.binance()
    try:
        symbols = [p['symbol'] for p in positions]
        tickers = await exchange.fetch_tickers(symbols)
        msg = "😴 **AFK Mode Active.** Signals paused.\n\nUpdate StockTrak with these safety levels:\n"

        for p in positions:
            ticker = tickers.get(p['symbol'])
            if not ticker:
                continue
            curr_price = ticker['last']

            if p.get('side', 'LONG') == 'LONG':
                afk_sl = curr_price * 0.96
                afk_tp = curr_price * 1.10
            else:
                afk_sl = curr_price * 1.04
                afk_tp = curr_price * 0.90

            msg += f"\n- **{p['symbol']}** ({p.get('side', 'LONG')}):\n"
            msg += f"  Safety SL: {fmt_price(afk_sl)}\n"
            msg += f"  Moon-shot TP: {fmt_price(afk_tp)}\n"

        if newly_registered:
            msg += "\n\nℹ️ Chat ID registered. Send /start to activate monitoring jobs."

        save_state(state)
        await update.message.reply_text(msg)
    finally:
        await exchange.close()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state, newly_registered = _ensure_chat_id(update, state)

    help_text = (
        "🦈 *Börsihai 2026 Swing Assistant*\n\n"
        "I am a 1H/4H swing trading bot tracking Binance coins against USDT. "
        "I use 4H EMA 200 for trend direction, 1H EMA 20/50 + MACD for entries, "
        "and ATR-based dynamic risk management.\n\n"
        "**Notification Hierarchy:**\n"
        "🚨 ACTION REQUIRED — Entries and full closures\n"
        "⚡ UPDATE — Partial TP1 hits and SL moves\n"
        "ℹ️ INFO — Status and heartbeat\n\n"
        "**Available Commands:**\n"
        "• `/status` - Portfolio overview with TP1 status per position\n"
        "• `/afk` - Pause signals, get safety SL (4%) and TP (10%) levels\n"
        "• `/ready` - Resume signal scanning\n"
        "• `/scan` - Run a market scan immediately\n"
        "• `/restart` - Restart the bot service\n"
        "• `/start` - Re-register the monitoring loop\n"
        "• `/help` - This message"
    )
    if newly_registered:
        help_text += "\n\nℹ️ Chat ID registered. Send /start to activate monitoring jobs."

    await update.message.reply_text(help_text, parse_mode='Markdown')


async def ready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state, newly_registered = _ensure_chat_id(update, state)
    state["bot_status"] = "ready"
    save_state(state)
    msg = "✅ Bot is Ready. Hunting for 1H/4H swing signals."
    if newly_registered:
        msg += "\n\nℹ️ Chat ID registered. Send /start to activate monitoring jobs."
    await update.message.reply_text(msg)


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger an immediate market scan and return a full summary."""
    from scanner import scan_market
    state = load_state()
    state, newly_registered = _ensure_chat_id(update, state)

    await update.message.reply_text("🔍 Running market scan now, this may take ~30 seconds...")

    scan_result = await scan_market()
    signal_list = scan_result.get("signals", [])
    metadata = scan_result.get("metadata", {})
    pairs_scanned = metadata.get("pairs_scanned", 0)
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    if not signal_list:
        msg = (
            f"✅ Manual scan complete ({now_str})\n"
            f"Pairs scanned: {pairs_scanned}\n"
            f"No signals found."
        )
        if newly_registered:
            msg += "\n\nℹ️ Chat ID registered. Send /start to activate monitoring jobs."
        await update.message.reply_text(msg)
        return

    # Deduplicate vs already-sent signals
    sent_signals = state.get("sent_signals", {})
    active_positions = state.get("active_positions", [])
    open_positions_set = {f"{p['symbol']}_{p.get('side', 'LONG')}" for p in active_positions}

    sent_pairs = []
    discarded_pairs = []
    new_sent = dict(sent_signals)

    for sig in signal_list:
        symbol = sig['symbol']
        side = sig['signal']
        score = sig.get('score', 0)
        score_display = sig.get('score_display', f"Score: {score}/100")

        if f"{symbol}_{side}" in open_positions_set:
            discarded_pairs.append(f"{symbol} ({side}) — {score}/100 [open pos]")
            continue
        price = sig['price']
        atr_val = sig.get('atr', 0)

        sig_key = f"{symbol}_{side}"
        if sig_key in sent_signals:
            discarded_pairs.append(f"{symbol} ({side}) — {score}/100 [already sent]")
            continue

        # Calculate preview levels
        initial_risk = ATR_MULTIPLIER * atr_val if atr_val > 0 else price * 0.04
        if side == "LONG":
            preview_sl = price - initial_risk
            preview_tp1 = price + (initial_risk * TP1_RR_RATIO)
        else:
            preview_sl = price + initial_risk
            preview_tp1 = price - (initial_risk * TP1_RR_RATIO)

        balance = state.get("portfolio_balance", DEFAULT_PORTFOLIO_BALANCE)
        order_size_usd = balance * POSITION_SIZE_PCT
        coin_qty = math.floor(order_size_usd / price) if price > 0 else 0

        path = sig.get('path', 'TA')
        path_label = "[TREND]" if path == "TA" else "[COUNTERTREND]"

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [InlineKeyboardButton("✅ Opened", callback_data=f"open_{side}_{symbol}_{atr_val:.4f}_{path}"),
             InlineKeyboardButton("❌ Ignore", callback_data=f"ignore_{symbol}_{side}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            f"🚨 **ACTION REQUIRED: {path_label} {side} Signal** 🚨\n"
            f"Symbol: {symbol}\n"
            f"{score_display}\n"
            f"Entry Price: {fmt_price(price)}\n"
            f"Stop Loss: {fmt_price(preview_sl)}\n"
            f"TP1 (1.5R): {fmt_price(preview_tp1)}\n"
            f"Order Size: ${order_size_usd:.2f} ({coin_qty} coins)"
        )
        await update.message.reply_text(text, reply_markup=reply_markup)
        new_sent[sig_key] = datetime.now(timezone.utc).isoformat()
        sent_pairs.append(f"{symbol} ({side}) — {score}/100")

    # Persist updated sent_signals
    state["sent_signals"] = new_sent
    save_state(state)

    # Send summary
    summary = f"📋 **Manual Scan Summary** ({now_str})\n"
    summary += f"Pairs scanned: {pairs_scanned}\n"
    summary += f"Alerts generated: {len(signal_list)} | Sent: {len(sent_pairs)} | Discarded: {len(discarded_pairs)}\n"
    if sent_pairs:
        summary += "\n**Sent:**\n" + "\n".join(f"  • {p}" for p in sent_pairs)
    if discarded_pairs:
        summary += "\n**Discarded:**\n" + "\n".join(f"  • {p}" for p in discarded_pairs)
    if newly_registered:
        summary += "\n\nℹ️ Chat ID registered. Send /start to activate monitoring jobs."
    await update.message.reply_text(summary)


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restart the systemd service. Bot process will be killed and relaunched."""
    import subprocess
    from config import SYSTEMD_SERVICE_NAME

    state = load_state()
    _ensure_chat_id(update, state)

    await update.message.reply_text(
        f"🔄 Restarting service `{SYSTEMD_SERVICE_NAME}`...\n"
        f"Bot will be back in a few seconds.",
        parse_mode="Markdown"
    )

    try:
        subprocess.Popen(
            ["sudo", "systemctl", "daemon-reload"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).wait(timeout=10)
        subprocess.Popen(
            ["sudo", "systemctl", "restart", SYSTEMD_SERVICE_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.error(f"Restart failed: {e}")
        await update.message.reply_text(f"⚠️ Restart command failed: {e}")


# ─── BUTTON HANDLERS ──────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    state = load_state()
    exchange = ccxt.binance()

    try:
        if data.startswith("open_"):
            await _handle_open(query, data, state, exchange)
        elif data.startswith("ignore_"):
            parts = data.split("_", 2)
            if len(parts) >= 3:
                symbol, side = parts[1], parts[2]
                sig_key = f"{symbol}_{side}"
                sent_signals = state.get("sent_signals", {})
                if sig_key in sent_signals:
                    del sent_signals[sig_key]
                    state["sent_signals"] = sent_signals
                    save_state(state)
            await query.edit_message_text("❌ Ignored signal.")
        elif data.startswith("slclosed_"):
            await _handle_sl_closed(query, data, state)
        elif data.startswith("slopen_"):
            await _handle_sl_open(query, data, state)
        elif data.startswith("halfclose_"):
            await _handle_half_close(query, data, state)
    except Exception as e:
        logger.error(f"Error handling button: {e}")
    finally:
        await exchange.close()


async def _handle_open(query, data, state, exchange):
    """Process 'Opened' button click — record position."""
    parts = data.split("_", 4)
    side = parts[1]
    symbol = parts[2]
    atr_val = float(parts[3]) if len(parts) > 3 else 0.0
    path = parts[4] if len(parts) > 4 else "TA"

    positions = state.get("active_positions", [])
    if len(positions) >= MAX_POSITIONS:
        await query.edit_message_text(f"Cannot open {symbol}: Max {MAX_POSITIONS} positions reached.")
        return

    ticker = await exchange.fetch_ticker(symbol)
    price = ticker['last']
    balance = state.get("portfolio_balance", DEFAULT_PORTFOLIO_BALANCE)
    available_cash = state.get("available_cash", balance)
    allocated_capital = balance * POSITION_SIZE_PCT

    if available_cash < allocated_capital:
        await query.edit_message_text(f"Cannot open {symbol}: Not enough available cash (${available_cash:.2f}).")
        return

    state["available_cash"] = available_cash - allocated_capital
    state["tied_capital"] = state.get("tied_capital", 0.0) + allocated_capital

    initial_risk = ATR_MULTIPLIER * atr_val if atr_val > 0 else price * 0.04
    if side == "LONG":
        sl = price - initial_risk
        tp1 = price + (initial_risk * TP1_RR_RATIO)
    else:
        sl = price + initial_risk
        tp1 = price - (initial_risk * TP1_RR_RATIO)

    coin_qty = math.floor(allocated_capital / price) if price > 0 else 0

    positions.append({
        "symbol": symbol,
        "side": side,
        "path": path,
        "entry_price": price,
        "allocated_capital": allocated_capital,
        "initial_risk": initial_risk,
        "current_sl": sl,
        "tp1_price": tp1,
        "tp1_hit": False,
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "denial_count": 0
    })
    state["active_positions"] = positions

    # Remove from sent_signals so it doesn't block future signals if this position closes
    sig_key = f"{symbol}_{side}"
    if "sent_signals" in state and sig_key in state["sent_signals"]:
        del state["sent_signals"][sig_key]

    save_state(state)

    log_trade("OPEN", symbol, side, price, sl, datetime.now(timezone.utc).timestamp())

    await query.edit_message_text(
        f"✅ Opened {side} on {symbol}\n"
        f"Entry: {fmt_price(price)}\n"
        f"SL: {fmt_price(sl)}\n"
        f"TP1: {fmt_price(tp1)}\n"
        f"Size: ${allocated_capital:.2f} ({coin_qty} coins)"
    )


async def _handle_sl_closed(query, data, state):
    """Process SL closure confirmation."""
    _, symbol = data.split("_", 1)
    positions = state.get("active_positions", [])
    kept_positions = []

    for p in positions:
        if p['symbol'] == symbol:
            entry = p['entry_price']
            sl = p['current_sl']
            pos_side = p.get('side', 'LONG')
            alloc = p.get('allocated_capital', state.get('portfolio_balance', DEFAULT_PORTFOLIO_BALANCE) * POSITION_SIZE_PCT)

            if pos_side == "LONG":
                net_pct = (sl - entry) / entry * 100 - 0.2
            else:
                net_pct = (entry - sl) / entry * 100 - 0.2

            pnl = alloc * (net_pct / 100)
            state['portfolio_balance'] = state.get('portfolio_balance', DEFAULT_PORTFOLIO_BALANCE) + pnl
            state['available_cash'] = state.get('available_cash', DEFAULT_PORTFOLIO_BALANCE) + alloc + pnl
            state['tied_capital'] = max(0.0, state.get('tied_capital', 0.0) - alloc)

            log_trade("CLOSE", symbol, pos_side, entry, sl, datetime.now(timezone.utc).timestamp(), pnl)
        else:
            kept_positions.append(p)

    state["active_positions"] = kept_positions
    save_state(state)
    await query.edit_message_text(f"✅ Confirmed closed: {symbol}.")


async def _handle_sl_open(query, data, state):
    """Process SL denial — keep position open."""
    _, symbol = data.split("_", 1)
    positions = state.get("active_positions", [])
    for p in positions:
        if p['symbol'] == symbol:
            p['denial_count'] = p.get('denial_count', 0) + 1
    state["active_positions"] = positions
    save_state(state)
    await query.edit_message_text(f"❌ Denied closure for {symbol}. Will re-check next cycle.")


async def _handle_half_close(query, data, state):
    """Process TP1 half-close confirmation."""
    _, symbol = data.split("_", 1)
    positions = state.get("active_positions", [])

    for p in positions:
        if p['symbol'] == symbol and not p.get('tp1_hit', False):
            p['tp1_hit'] = True
            entry = p['entry_price']
            side = p.get('side', 'LONG')
            alloc = p.get('allocated_capital', 0)
            half_alloc = alloc / 2.0

            tp1 = p.get('tp1_price', entry)
            if side == "LONG":
                net_pct = (tp1 - entry) / entry * 100 - 0.2
                be_sl = entry * 1.002
            else:
                net_pct = (entry - tp1) / entry * 100 - 0.2
                be_sl = entry * 0.998

            pnl = half_alloc * (net_pct / 100)

            state['portfolio_balance'] = state.get('portfolio_balance', DEFAULT_PORTFOLIO_BALANCE) + pnl
            state['available_cash'] = state.get('available_cash', 0) + half_alloc + pnl
            state['tied_capital'] = max(0.0, state.get('tied_capital', 0.0) - half_alloc)

            p['allocated_capital'] = half_alloc
            p['current_sl'] = be_sl

            log_trade("PARTIAL_CLOSE", symbol, side, entry, tp1, datetime.now(timezone.utc).timestamp(), pnl)

    state["active_positions"] = positions
    save_state(state)
    await query.edit_message_text(
        f"⚡ TP1 half-close confirmed for {symbol}.\n"
        f"SL moved to break-even. Remaining 50% running — will alert on MACD exit."
    )
