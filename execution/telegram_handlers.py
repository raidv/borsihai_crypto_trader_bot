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
    DEFAULT_TIMEFRAME, TIMEFRAME_PAIRINGS, parse_timeframe, VALID_TIMEFRAMES,
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

    entry_tf = state.get("timeframe", DEFAULT_TIMEFRAME)
    pairing = TIMEFRAME_PAIRINGS.get(entry_tf, TIMEFRAME_PAIRINGS[DEFAULT_TIMEFRAME])
    trend_tf = pairing["trend"]
    register_jobs(context, chat_id, entry_tf)
    await update.message.reply_text(
        f"🦈 Börsihai 2026 Swing Assistant is online.\n"
        f"Active timeframe: **{entry_tf.upper()}** (Trend filter: {trend_tf.upper()})"
    )


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
                afk_sl_calc = curr_price * 0.96
                afk_sl = max(p.get('current_sl', 0.0), afk_sl_calc)
                afk_tp = curr_price * 1.10
            else:
                afk_sl_calc = curr_price * 1.04
                orig_sl = p.get('current_sl', float('inf'))
                afk_sl = min(orig_sl, afk_sl_calc)
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
        "I am a configurable timeframe swing trading bot tracking Binance coins against USDT. "
        "I use EMA 200 on the trend timeframe for direction, EMA 20/50 + MACD on the entry timeframe for entries, "
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
        "• `/summary` - Morning brief: actionable positions and high-score alerts\n"
        "• `/timeframe <tf>` - Change scan timeframe (e.g. /timeframe 4h, /timeframe 1d)\n"
        "• `/detail <coin>` - Show full alert details for a pending signal\n"
        "• `/restart` - Restart the bot service\n"
        "• `/start` - Re-register the monitoring loop\n"
        "• `/sl <coin> <price>` - Manually update SL for an open position\n"
        "• `/balance <amount>` - Set portfolio balance manually\n"
        "• `/long <coin>` - Manually open a LONG position\n"
        "• `/short <coin>` - Manually open a SHORT position\n"
        "• `/close <coin> [price]` - Manually close a position\n"
        "• `/clean` - Clear un-interacted alerts from bot memory\n"
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
    entry_tf = state.get("timeframe", DEFAULT_TIMEFRAME)
    msg = f"✅ Bot is Ready. Hunting for {entry_tf.upper()} swing signals."
    if newly_registered:
        msg += "\n\nℹ️ Chat ID registered. Send /start to activate monitoring jobs."
    await update.message.reply_text(msg)


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger an immediate market scan and return a full summary."""
    from scanner import scan_market
    state = load_state()
    state, newly_registered = _ensure_chat_id(update, state)

    await update.message.reply_text("🔍 Running market scan now, this may take ~30 seconds...")

    scan_result = await scan_market(entry_tf=state.get("timeframe", DEFAULT_TIMEFRAME))
    signal_list = scan_result.get("signals", [])
    metadata = scan_result.get("metadata", {})
    pairs_scanned = metadata.get("pairs_scanned", 0)
    active_tf = metadata.get("entry_tf", state.get("timeframe", DEFAULT_TIMEFRAME))
    tf_label = f"[{active_tf.upper()}]"
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    if not signal_list:
        msg = (
            f"✅ Manual scan complete {tf_label} ({now_str})\n"
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

    # Fresh pending signals (drops ones from previous scans automatically)
    new_pending = {}
    new_sent = dict(sent_signals)
    summary_lines = []
    discarded_pairs = []

    for sig in signal_list:
        symbol = sig['symbol']
        side = sig['signal']
        score = sig.get('score', 0)
        price = sig['price']
        atr_val = sig.get('atr', 0)
        path = sig.get('path', 'TA')
        entry_tf_sig = sig.get('entry_tf', state.get("timeframe", DEFAULT_TIMEFRAME))
        sig_key = f"{symbol}_{side}"
        base_coin = symbol.split('/')[0]

        if sig_key in open_positions_set:
            summary_lines.append(f"  📌 {base_coin} ({side}) — {score}/100 (POSITION OPEN)")
            continue

        initial_risk = ATR_MULTIPLIER * atr_val if atr_val > 0 else price * 0.04
        if side == "LONG":
            preview_sl = price - initial_risk
            preview_tp1 = price + (initial_risk * TP1_RR_RATIO)
        else:
            preview_sl = price + initial_risk
            preview_tp1 = price - (initial_risk * TP1_RR_RATIO)

        balance = state.get("portfolio_balance", DEFAULT_PORTFOLIO_BALANCE)
        order_size_usd = balance * POSITION_SIZE_PCT

        new_pending[base_coin.upper()] = {
            "symbol": symbol,
            "side": side,
            "path": path,
            "score": score,
            "score_display": sig.get('score_display', f"Score: {score}/100"),
            "price": price,
            "atr_val": atr_val,
            "preview_sl": preview_sl,
            "preview_tp1": preview_tp1,
            "order_size_usd": order_size_usd,
            "entry_tf": entry_tf_sig,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        path_label = "[TREND]" if path == "TA" else "[CT]"
        summary_lines.append(f"  🔔 {base_coin} ({side}) {path_label} — {score}/100  →  /detail {base_coin.lower()}")
        new_sent[sig_key] = datetime.now(timezone.utc).isoformat()

    state["sent_signals"] = new_sent
    state["pending_signals"] = new_pending
    save_state(state)

    # Send summary only
    num_new = len(summary_lines) - sum(1 for l in summary_lines if "POSITION OPEN" in l)
    summary = f"📋 **Manual Scan Summary {tf_label}** ({now_str})\n"
    summary += f"Pairs scanned: {pairs_scanned} | New: {num_new} | Skipped: {len(discarded_pairs)}\n"
    if summary_lines:
        summary += "\n**Alerts** (use /detail <coin> for full details):\n"
        summary += "\n".join(summary_lines)
    if discarded_pairs:
        summary += "\n**Skipped:** " + ", ".join(discarded_pairs)
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
        elif data.startswith("slraised_"):
            await _handle_sl_raised(query, data, state)
    except Exception as e:
        await update.message.reply_text(f"❌ Error handling button: {e}")
    finally:
        await exchange.close()


async def update_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually update the Stop Loss for a position. Usage: /sl <symbol> <price>"""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /sl <symbol> <price>\nExample: /sl SOL 150.5")
        return
        
    symbol = args[0].upper()
    if "/" not in symbol and not symbol.endswith("USDT"):
        symbol += "/USDT"
        
    try:
        new_sl = float(args[1])
    except ValueError:
        await update.message.reply_text(f"❌ Invalid price format: {args[1]}")
        return
        
    state = load_state()
    positions = state.get("active_positions", [])
    
    updated = False
    for p in positions:
        if p["symbol"].upper() == symbol:
            p["current_sl"] = new_sl
            updated = True
            break
            
    if not updated:
        await update.message.reply_text(f"❌ Could not find an open position for {symbol}.")
        return
        
    state["active_positions"] = positions
    save_state(state)
    await update.message.reply_text(f"✅ Stop Loss for {symbol} updated to {fmt_price(new_sl)}.")


async def timeframe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the active scan timeframe. Usage: /timeframe <tf>
    Examples: /timeframe 1h  /timeframe 4h  /timeframe 1d  /timeframe 15m"""
    from bot import register_jobs
    args = context.args

    if not args:
        state = load_state()
        entry_tf = state.get("timeframe", DEFAULT_TIMEFRAME)
        pairing = TIMEFRAME_PAIRINGS.get(entry_tf, TIMEFRAME_PAIRINGS[DEFAULT_TIMEFRAME])
        trend_tf = pairing["trend"]
        valid_list = ", ".join(sorted(VALID_TIMEFRAMES.keys()))
        await update.message.reply_text(
            f"⌛ Current timeframe: **{entry_tf.upper()}** (Trend: {trend_tf.upper()})\n\n"
            f"Usage: /timeframe <value>\n"
            f"Supported: {valid_list}\n"
            f"Aliases: 1h, h, 1hour | 1d, d, daily | 15m, 15min, 15minutes ..."
        )
        return

    raw = args[0]
    tf = parse_timeframe(raw)
    if tf is None:
        valid_list = ", ".join(sorted(VALID_TIMEFRAMES.keys()))
        await update.message.reply_text(
            f"⚠️ Unsupported timeframe: **{raw}**\n"
            f"Supported: {valid_list}\n"
            f"Bot continues with current timeframe."
        )
        return

    state = load_state()
    state, _ = _ensure_chat_id(update, state)
    old_tf = state.get("timeframe", DEFAULT_TIMEFRAME)
    state["timeframe"] = tf
    save_state(state)

    pairing = TIMEFRAME_PAIRINGS.get(tf, TIMEFRAME_PAIRINGS[DEFAULT_TIMEFRAME])
    trend_tf = pairing["trend"]
    chat_id = state.get("chat_id", update.effective_chat.id)

    # Re-register jobs with new scan interval
    register_jobs(context, chat_id, tf)

    await update.message.reply_text(
        f"✅ Timeframe changed: **{old_tf.upper()}** → **{tf.upper()}**\n"
        f"Trend filter: {trend_tf.upper()}\n"
        f"Scan interval: every {pairing['scan_interval']} min\n"
        f"Jobs re-registered. Next scan will use the new timeframe."
    )


async def detail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show full alert details for a pending signal. Usage: /detail <coin>
    Example: /detail SOL  or  /detail sol  (case-insensitive)
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /detail <coin>\nExample: /detail SOL\n\nUse /scan to see available signals."
        )
        return

    coin = args[0].strip().upper()
    # Strip /USDT suffix if user typed full symbol
    coin = coin.replace("/USDT", "").replace("USDT", "")

    state = load_state()
    pending = state.get("pending_signals", {})

    if not pending:
        await update.message.reply_text(
            "ℹ️ No pending signals in memory.\nRun /scan to generate fresh signals."
        )
        return

    # Case-insensitive lookup
    sig_data = None
    matched_coin = None
    for key in pending:
        if key.upper() == coin:
            sig_data = pending[key]
            matched_coin = key
            break

    if sig_data is None:
        available = ", ".join(sorted(pending.keys()))
        await update.message.reply_text(
            f"❌ No pending alert for **{coin}**.\n"
            f"Available: {available or 'none'}\n"
            f"Use /scan to refresh signals."
        )
        return

    symbol = sig_data["symbol"]
    side = sig_data["side"]
    path = sig_data.get("path", "TA")
    score = sig_data.get("score", 0)
    score_display = sig_data.get("score_display", f"Score: {score}/100")
    price = sig_data["price"]
    atr_val = sig_data.get("atr_val", 0)
    preview_sl = sig_data["preview_sl"]
    preview_tp1 = sig_data["preview_tp1"]
    order_size_usd = sig_data["order_size_usd"]
    entry_tf = sig_data.get("entry_tf", state.get("timeframe", DEFAULT_TIMEFRAME))
    tf_label = f"[{entry_tf.upper()}]"
    path_label = "[TREND]" if path == "TA" else "[COUNTERTREND]"
    coin_qty = math.floor(order_size_usd / price) if price > 0 else 0

    keyboard = [
        [InlineKeyboardButton("✅ Opened", callback_data=f"open_{side}_{symbol}_{atr_val:.4f}_{path}"),
         InlineKeyboardButton("❌ Ignore", callback_data=f"ignore_{symbol}_{side}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"🚨 **ACTION REQUIRED {tf_label}: {path_label} {side} Signal** 🚨\n"
        f"Symbol: {symbol}\n"
        f"{score_display}\n"
        f"Entry Price: {fmt_price(price)}\n"
        f"Stop Loss: {fmt_price(preview_sl)}\n"
        f"TP1 (1.5R): {fmt_price(preview_tp1)}\n"
        f"Order Size: ${order_size_usd:.2f} ({coin_qty} coins)"
    )
    await update.message.reply_text(text, reply_markup=reply_markup)


async def clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear sent_signals and pending_signals from state.json."""
    state = load_state()
    sent_signals = state.get("sent_signals", {})
    pending_signals = state.get("pending_signals", {})
    count = len(sent_signals) + len(pending_signals)
    state["sent_signals"] = {}
    state["pending_signals"] = {}
    save_state(state)
    await update.message.reply_text(f"🧹 Cleaned up {count} old alerts and pending signals. Future signals are now unblocked.")


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update portfolio balance. Usage: /balance <amount>"""
    args = context.args
    if not args:
        state = load_state()
        bal = state.get("portfolio_balance", DEFAULT_PORTFOLIO_BALANCE)
        await update.message.reply_text(f"Current recorded balance is: {fmt_price(bal)}\nUsage: /balance <amount>")
        return

    try:
        new_balance = float(args[0].replace(",", ""))
    except ValueError:
        await update.message.reply_text("Invalid amount. Usage: /balance 25000.50")
        return

    state = load_state()
    old_balance = state.get("portfolio_balance", DEFAULT_PORTFOLIO_BALANCE)
    diff = new_balance - old_balance

    state["portfolio_balance"] = new_balance
    state["available_cash"] = state.get("available_cash", old_balance) + diff
    save_state(state)

    await update.message.reply_text(f"✅ Balance updated: {fmt_price(old_balance)} → {fmt_price(new_balance)}")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate a morning brief of actionable items and high-score signals."""
    import random
    import ccxt.async_support as ccxt
    
    await update.message.reply_text("🔄 Compiling your summary, please wait...")

    state = load_state()
    pending = state.get("pending_signals", {})
    active_positions = state.get("active_positions", [])
    
    # 1. High Score Pending Signals (>= 85)
    high_score_signals = []
    for coin, sig in pending.items():
        if sig.get('score', 0) >= 85:
            high_score_signals.append((coin, sig))
            
    # Send detailed alerts for high score signals first
    for coin, sig in high_score_signals:
        symbol = sig["symbol"]
        side = sig["side"]
        path = sig.get("path", "TA")
        score = sig.get("score", 0)
        score_display = sig.get("score_display", f"Score: {score}/100")
        price = sig["price"]
        atr_val = sig.get("atr_val", 0)
        preview_sl = sig["preview_sl"]
        preview_tp1 = sig["preview_tp1"]
        order_size_usd = sig["order_size_usd"]
        entry_tf = sig.get("entry_tf", state.get("timeframe", DEFAULT_TIMEFRAME))
        tf_label = f"[{entry_tf.upper()}]"
        path_label = "[TREND]" if path == "TA" else "[COUNTERTREND]"
        coin_qty = math.floor(order_size_usd / price) if price > 0 else 0

        keyboard = [
            [InlineKeyboardButton("✅ Opened", callback_data=f"open_{side}_{symbol}_{atr_val:.4f}_{path}"),
             InlineKeyboardButton("❌ Ignore", callback_data=f"ignore_{symbol}_{side}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            f"🌟 **HIGH SCORE ALERT {tf_label}: {path_label} {side} Signal** 🌟\n"
            f"Symbol: {symbol}\n"
            f"{score_display}\n"
            f"Entry Price: {fmt_price(price)}\n"
            f"Stop Loss: {fmt_price(preview_sl)}\n"
            f"TP1 (1.5R): {fmt_price(preview_tp1)}\n"
            f"Order Size: ${order_size_usd:.2f} ({coin_qty} coins)"
        )
        await update.message.reply_text(text, reply_markup=reply_markup)

    # 2. Actionable Open Positions
    actionable_lines = []
    if active_positions:
        exchange = ccxt.binance()
        try:
            symbols = list(set(p['symbol'] for p in active_positions))
            tickers = await exchange.fetch_tickers(symbols)
            
            for p in active_positions:
                symbol = p['symbol']
                side = p.get('side', 'LONG')
                ticker = tickers.get(symbol)
                if not ticker:
                    continue
                current_price = ticker['last']
                sl = p['current_sl']
                tp1 = p.get('tp1_price', 0)
                tp1_hit = p.get('tp1_hit', False)
                
                # Check SL breaches
                if (side == "LONG" and current_price <= sl) or (side == "SHORT" and current_price >= sl):
                    actionable_lines.append(f"⚠️ **{symbol}** SL {fmt_price(sl)} breached (Price: {fmt_price(current_price)}). Consider /close.")
                    
                # Check TP hits
                if not tp1_hit and tp1 > 0:
                    if (side == "LONG" and current_price >= tp1) or (side == "SHORT" and current_price <= tp1):
                        actionable_lines.append(f"🎯 **{symbol}** TP1 {fmt_price(tp1)} hit! Consider half-close and raise SL.")
                        
                # Next TP check
                next_tp = p.get('next_tp_price')
                if tp1_hit and next_tp:
                    if (side == "LONG" and current_price >= next_tp) or (side == "SHORT" and current_price <= next_tp):
                        lvl = p.get('next_tp_level', 2)
                        actionable_lines.append(f"🎯 **{symbol}** Next target TP{lvl} {fmt_price(next_tp)} reached! Consider raising SL.")
        except Exception as e:
            logger.error(f"Error fetching tickers for summary: {e}")
            actionable_lines.append("❌ Could not fetch live prices for open positions.")
        finally:
            await exchange.close()

    # 3. Compile Master Summary
    total_pending = len(pending)
    
    if not high_score_signals and not actionable_lines and total_pending == 0:
        all_clear_msgs = [
            "All good in the neighbourhood, nothing to report! 🌴",
            "Markets are quiet. Your portfolio is safe. Enjoy your coffee ☕",
            "No high-score alerts, no breached stops. Smooth sailing captain! ⛵",
            "Nothing doing today! Take a break from the charts. 🎮"
        ]
        msg = f"🌅 **Morning Brief**\n\n{random.choice(all_clear_msgs)}"
    else:
        msg = f"🌅 **Morning Brief**\n"
        if high_score_signals:
            msg += f"\n🌟 **Top Picks:** {len(high_score_signals)} A+ setups sent above."
        msg += f"\n📊 **Total Pending Signals:** {total_pending} available in /scan"
        
        if actionable_lines:
            msg += "\n\n🚨 **Actionable Positions:**\n" + "\n".join(actionable_lines)
        else:
            msg += "\n\n✅ All open positions are comfortably within limits."

    await update.message.reply_text(msg)



async def close_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close a position manually. Usage: /close <symbol> [price]"""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /close <symbol> [price]\nExample: /close SOL or /close sol 150.5")
        return
    
    symbol = args[0].upper()
    if "/" not in symbol and not symbol.endswith("USDT"):
        symbol += "/USDT"
        
    price_arg = args[1] if len(args) > 1 else None
    
    state = load_state()
    positions = state.get("active_positions", [])
    
    target_pos = None
    target_idx = -1
    for i, p in enumerate(positions):
        if p["symbol"].upper() == symbol:
            target_pos = p
            target_idx = i
            break
            
    if not target_pos:
        await update.message.reply_text(f"❌ Position not found for {symbol}.")
        return
        
    price = 0.0
    if price_arg:
        try:
            price = float(price_arg)
        except ValueError:
            await update.message.reply_text(f"❌ Invalid price format: {price_arg}")
            return
    else:
        exchange = ccxt.binance()
        try:
            ticker = await exchange.fetch_ticker(target_pos["symbol"])
            price = ticker['last']
        except Exception as e:
            await update.message.reply_text(f"❌ Could not fetch market price for {symbol}: {e}")
            await exchange.close()
            return
        finally:
            await exchange.close()
            
    from state_manager import log_trade
    from config import DEFAULT_PORTFOLIO_BALANCE, POSITION_SIZE_PCT

    # ── Release capital back to portfolio ──
    alloc = target_pos.get("allocated_capital", state.get("portfolio_balance", DEFAULT_PORTFOLIO_BALANCE) * POSITION_SIZE_PCT)
    entry = target_pos["entry_price"]
    pos_side = target_pos.get("side", "LONG")
    tp1_hit = target_pos.get("tp1_hit", False)

    # If TP1 was already hit, only 50% of the original allocation is still running
    running_alloc = alloc if not tp1_hit else alloc  # allocated_capital already halved at TP1 hit

    if pos_side == "LONG":
        net_pct = (price - entry) / entry * 100 - 0.2  # 0.2% for spread/fees
    else:
        net_pct = (entry - price) / entry * 100 - 0.2

    pnl = running_alloc * (net_pct / 100)

    state["portfolio_balance"] = state.get("portfolio_balance", DEFAULT_PORTFOLIO_BALANCE) + pnl
    state["available_cash"] = state.get("available_cash", DEFAULT_PORTFOLIO_BALANCE) + running_alloc + pnl
    state["tied_capital"] = max(0.0, state.get("tied_capital", 0.0) - running_alloc)

    log_trade("CLOSE", target_pos["symbol"], pos_side, entry, price, datetime.now(timezone.utc).timestamp(), pnl)

    del positions[target_idx]
    state["active_positions"] = positions
    save_state(state)

    pnl_sign = "+" if pnl >= 0 else ""
    await update.message.reply_text(
        f"✅ Closed {pos_side} on {target_pos['symbol']} at {fmt_price(price)}.\n"
        f"P&L: {pnl_sign}${pnl:.2f} | New balance: ${state['portfolio_balance']:.2f}"
    )


async def _manual_position(update: Update, context: ContextTypes.DEFAULT_TYPE, side: str):
    args = context.args
    if not args:
        await update.message.reply_text(f"Usage: /{side.lower()} <symbol>\nExample: /{side.lower()} SOL")
        return
        
    symbol = args[0].upper()
    if "/" not in symbol and not symbol.endswith("USDT"):
        symbol += "/USDT"
        
    state = load_state()
    exchange = ccxt.binance()
    
    try:
        from config import DEFAULT_PORTFOLIO_BALANCE, POSITION_SIZE_PCT, ATR_MULTIPLIER, TP1_RR_RATIO
        ticker = await exchange.fetch_ticker(symbol)
        price = ticker['last']
        
        # Calculate ATR for dynamic risk
        atr_val = 0.0
        try:
            import pandas as pd
            import pandas_ta as ta
            from scanner import fetch_ohlcv
            df = await fetch_ohlcv(exchange, symbol, "1h", limit=50)
            if df is not None and len(df) >= 15:
                df.ta.atr(length=14, append=True)
                val = df['ATRr_14'].dropna()
                if not val.empty:
                    atr_val = val.iloc[-1]
        except Exception as atr_err:
            logger.warning(f"Failed to calculate ATR for {symbol}, falling back to 4%: {atr_err}")

        initial_risk = ATR_MULTIPLIER * atr_val if atr_val > 0 else price * 0.04
        
        if side == "LONG":
            sl = price - initial_risk
            tp1 = price + (initial_risk * TP1_RR_RATIO)
        else:
            sl = price + initial_risk
            tp1 = price - (initial_risk * TP1_RR_RATIO)
            
        balance = state.get("portfolio_balance", DEFAULT_PORTFOLIO_BALANCE)
        available_cash = state.get("available_cash", balance)
        allocated_capital = balance * POSITION_SIZE_PCT
        
        warning_msg = ""
        if available_cash < allocated_capital:
            warning_msg = f"\n⚠️ Warning: Not enough available cash (${available_cash:.2f})."
            
        state["available_cash"] = available_cash - allocated_capital
        state["tied_capital"] = state.get("tied_capital", 0.0) + allocated_capital
        
        coin_qty = math.floor(allocated_capital / price) if price > 0 else 0
        
        positions = state.get("active_positions", [])
        positions.append({
            "symbol": symbol,
            "side": side,
            "path": "MANUAL",
            "entry_price": price,
            "allocated_capital": allocated_capital,
            "initial_risk": initial_risk,
            "current_sl": sl,
            "tp1_price": tp1,
            "tp1_hit": False,
            "timestamp": datetime.now(timezone.utc).timestamp(),
            "denial_count": 0,
            "entry_tf": state.get("timeframe", DEFAULT_TIMEFRAME)
        })
        state["active_positions"] = positions
        save_state(state)
        
        from state_manager import log_trade
        log_trade("OPEN", symbol, side, price, sl, datetime.now(timezone.utc).timestamp())
        
        await update.message.reply_text(
            f"✅ Opened {side} on {symbol}\n"
            f"Entry: {fmt_price(price)}\n"
            f"SL: {fmt_price(sl)}\n"
            f"TP1: {fmt_price(tp1)}\n"
            f"Size: ${allocated_capital:.2f} ({coin_qty} coins){warning_msg}"
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Error opening {symbol}: {e}")
    finally:
        await exchange.close()


async def manual_long(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _manual_position(update, context, "LONG")


async def manual_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _manual_position(update, context, "SHORT")


async def _handle_open(query, data, state, exchange):
    """Process 'Opened' button click — record position."""
    parts = data.split("_", 4)
    side = parts[1]
    symbol = parts[2]
    atr_val = float(parts[3]) if len(parts) > 3 else 0.0
    path = parts[4] if len(parts) > 4 else "TA"

    positions = state.get("active_positions", [])

    ticker = await exchange.fetch_ticker(symbol)
    price = ticker['last']
    balance = state.get("portfolio_balance", 25000.0) # Fallback, override using actual config below 
    from config import DEFAULT_PORTFOLIO_BALANCE, ATR_MULTIPLIER, TP1_RR_RATIO, POSITION_SIZE_PCT
    balance = state.get("portfolio_balance", DEFAULT_PORTFOLIO_BALANCE)
    available_cash = state.get("available_cash", balance)
    allocated_capital = balance * POSITION_SIZE_PCT

    warning_msg = ""
    if available_cash < allocated_capital:
        warning_msg = f"\n⚠️ Warning: Not enough available cash (${available_cash:.2f})."

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
        "denial_count": 0,
        "entry_tf": state.get("timeframe", DEFAULT_TIMEFRAME)
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
        f"Size: ${allocated_capital:.2f} ({coin_qty} coins){warning_msg}"
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

            from config import TP_STEP_RR
            p['allocated_capital'] = half_alloc
            p['current_sl'] = be_sl
            
            # Initiate dynamic TP tracking for the remaining 50%
            initial_risk = p.get('initial_risk', entry * 0.04)
            if side == "LONG":
                p['next_tp_price'] = tp1 + (initial_risk * TP_STEP_RR)
                p['prev_tp_price'] = tp1
            else:
                p['next_tp_price'] = tp1 - (initial_risk * TP_STEP_RR)
                p['prev_tp_price'] = tp1
            p['next_tp_level'] = 2

            log_trade("PARTIAL_CLOSE", symbol, side, entry, tp1, datetime.now(timezone.utc).timestamp(), pnl)

    state["active_positions"] = positions
    save_state(state)
    await query.edit_message_text(
        f"⚡ TP1 half-close confirmed for {symbol}.\n"
        f"SL moved to break-even. Remaining 50% running — will alert on TP2 or MACD exit."
    )


async def _handle_sl_raised(query, data, state):
    """Process SL Raised confirmation."""
    _, symbol = data.split("_", 1)
    positions = state.get("active_positions", [])

    for p in positions:
        if p['symbol'] == symbol:
            side = p.get('side', 'LONG')
            new_sl = p.get('prev_tp_price', p['entry_price'])
            p['current_sl'] = new_sl
            lvl = p.get('next_tp_level', 3) - 1
            
            state["active_positions"] = positions
            save_state(state)
            
            await query.edit_message_text(
                f"✅ SL Raised for {symbol} to {fmt_price(new_sl)}.\n"
                f"Continuing to ride trend... we will notify if TP{lvl+1} is hit at {fmt_price(p.get('next_tp_price', 0))}."
            )
            return

    await query.edit_message_text(f"❌ Could not find active position for {symbol}.")
