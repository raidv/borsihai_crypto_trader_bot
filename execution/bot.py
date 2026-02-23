import asyncio
import logging
import math
import os
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import ccxt.async_support as ccxt
from dotenv import load_dotenv
import pandas as pd
import pandas_ta as ta

from state_manager import load_state, save_state, log_trade
from scanner import scan_market

load_dotenv()

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("Bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEBUG_RUN_IMMEDIATELY = False

MAX_POSITIONS = 10
POSITION_SIZE_PCT = 0.10  # 10% of portfolio per position
ATR_MULTIPLIER = 2.0      # SL = Entry +/- (ATR * 2.0)
TP1_RR_RATIO = 1.5        # TP1 at 1.5x risk

_has_run_once = False

# ─── PRICE FORMATTER ──────────────────────────────────────────────────

def fmt_price(price):
    """Adaptive price formatting for all price ranges."""
    if price == 0:
        return "$0"
    abs_price = abs(price)
    if abs_price >= 1.0:
        return f"${price:.2f}"
    elif abs_price >= 0.01:
        return f"${price:.4f}"
    elif abs_price >= 0.0001:
        return f"${price:.6f}"
    else:
        return f"${price:.8f}"

# ─── COMMAND HANDLERS ──────────────────────────────────────────────────

def _register_jobs(context_or_jq, chat_id):
    """Register both the signal_scanner and position_monitor jobs."""
    jq = context_or_jq if hasattr(context_or_jq, 'run_repeating') else context_or_jq.job_queue
    
    # Remove existing jobs
    for name in ["signal_scanner", "position_monitor"]:
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()
    
    # Signal scanner: runs every 60s, checks internally for hourly alignment
    jq.run_repeating(signal_scanner, interval=60, first=0, chat_id=chat_id, name="signal_scanner")
    # Position monitor: runs every 60s, checks internally for 5-min alignment
    jq.run_repeating(position_monitor, interval=60, first=30, chat_id=chat_id, name="position_monitor")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = load_state()
    state["chat_id"] = chat_id
    save_state(state)
    
    _register_jobs(context, chat_id)
    await update.message.reply_text("🦈 Börsihai 2026 Swing Assistant is online. Monitoring 1H/4H strategy.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
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
    await update.message.reply_text(msg)

async def afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["bot_status"] = "afk"
    
    positions = state.get("active_positions", [])
    if not positions:
        save_state(state)
        await update.message.reply_text("😴 Bot is now AFK. No incoming signals.")
        return
        
    exchange = ccxt.binance()
    try:
        symbols = [p['symbol'] for p in positions]
        tickers = await exchange.fetch_tickers(symbols)
        msg = "😴 **AFK Mode Active.** Signals paused.\n\nUpdate StockTrak with these safety levels:\n"
        
        for p in positions:
            ticker = tickers.get(p['symbol'])
            if not ticker: continue
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
            
        save_state(state)
        await update.message.reply_text(msg)
    finally:
        await exchange.close()

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "• `/start` - Re-register the monitoring loop\n"
        "• `/help` - This message"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def ready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["bot_status"] = "ready"
    save_state(state)
    await update.message.reply_text("✅ Bot is Ready. Hunting for 1H/4H swing signals.")

# ─── BUTTON HANDLERS ──────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    state = load_state()
    exchange = ccxt.binance()
    
    try:
        if data.startswith("open_"):
            # Format: open_LONG_BTC/USDT_ATR
            parts = data.split("_", 3)
            side = parts[1]
            symbol = parts[2]
            atr_val = float(parts[3]) if len(parts) > 3 else 0.0
            
            positions = state.get("active_positions", [])
            
            if len(positions) >= MAX_POSITIONS:
                await query.edit_message_text(f"Cannot open {symbol}: Max {MAX_POSITIONS} positions reached.")
                return
                
            ticker = await exchange.fetch_ticker(symbol)
            price = ticker['last']
            balance = state.get("portfolio_balance", 25000.0)
            available_cash = state.get("available_cash", balance)
            allocated_capital = balance * POSITION_SIZE_PCT
            
            if available_cash < allocated_capital:
                await query.edit_message_text(f"Cannot open {symbol}: Not enough available cash (${available_cash:.2f}).")
                return
                
            state["available_cash"] = available_cash - allocated_capital
            state["tied_capital"] = state.get("tied_capital", 0.0) + allocated_capital
            
            # ATR-based SL
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
            save_state(state)
            
            log_trade("OPEN", symbol, side, price, sl, datetime.now(timezone.utc).timestamp())
            
            await query.edit_message_text(
                f"✅ Opened {side} on {symbol}\n"
                f"Entry: {fmt_price(price)}\n"
                f"SL: {fmt_price(sl)}\n"
                f"TP1: {fmt_price(tp1)}\n"
                f"Size: ${allocated_capital:.2f} ({coin_qty} coins)"
            )
            
        elif data.startswith("ignore_"):
            await query.edit_message_text("❌ Ignored signal.")
            
        elif data.startswith("slclosed_"):
            _, symbol = data.split("_", 1)
            positions = state.get("active_positions", [])
            kept_positions = []
            
            for p in positions:
                if p['symbol'] == symbol:
                    entry = p['entry_price']
                    sl = p['current_sl']
                    pos_side = p.get('side', 'LONG')
                    alloc = p.get('allocated_capital', state.get('portfolio_balance', 25000.0) * POSITION_SIZE_PCT)
                    
                    if pos_side == "LONG":
                        net_pct = (sl - entry) / entry * 100 - 0.2
                    else:
                        net_pct = (entry - sl) / entry * 100 - 0.2
                    
                    pnl = alloc * (net_pct / 100)
                    state['portfolio_balance'] = state.get('portfolio_balance', 25000.0) + pnl
                    state['available_cash'] = state.get('available_cash', 25000.0) + alloc + pnl
                    state['tied_capital'] = max(0.0, state.get('tied_capital', 0.0) - alloc)
                    
                    log_trade("CLOSE", symbol, pos_side, entry, sl, datetime.now(timezone.utc).timestamp(), pnl)
                else:
                    kept_positions.append(p)
                    
            state["active_positions"] = kept_positions
            save_state(state)
            await query.edit_message_text(f"✅ Confirmed closed: {symbol}.")
            
        elif data.startswith("slopen_"):
            _, symbol = data.split("_", 1)
            positions = state.get("active_positions", [])
            for p in positions:
                if p['symbol'] == symbol:
                    p['denial_count'] = p.get('denial_count', 0) + 1
            state["active_positions"] = positions
            save_state(state)
            await query.edit_message_text(f"❌ Denied closure for {symbol}. Will re-check next cycle.")
            
        elif data.startswith("halfclose_"):
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
                    
                    state['portfolio_balance'] = state.get('portfolio_balance', 25000.0) + pnl
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

    except Exception as e:
        logger.error(f"Error handling button: {e}")
    finally:
        await exchange.close()

# ─── SIGNAL SCANNER (runs every hour) ─────────────────────────────────

async def signal_scanner(context: ContextTypes.DEFAULT_TYPE):
    global _has_run_once
    now = datetime.now(timezone.utc)
    
    force_run = False
    if DEBUG_RUN_IMMEDIATELY and not _has_run_once:
        force_run = True
        _has_run_once = True
        logger.info("Forcing initial debug market scan...")
    
    # Only run at minute :01 of each hour
    if not force_run and now.minute != 1:
        return
        
    logger.info(f"Running hourly signal scan at {now.strftime('%H:%M:%S')}")
    state = load_state()
    
    if state.get("bot_status") != "ready":
        return
        
    signals = await scan_market()
    
    # Deduplicate: check which signals were already sent
    sent_signals = state.get("sent_signals", {})
    new_sent = {}
    
    for sig in signals:
        symbol = sig['symbol']
        side = sig['signal']
        score = sig.get('score', 0)
        price = sig['price']
        atr_val = sig.get('atr', 0)
        
        # Skip if we already sent this exact (symbol, side) signal recently
        sig_key = f"{symbol}_{side}"
        if sig_key in sent_signals:
            logger.info(f"Skipping duplicate signal: {sig_key}")
            continue
        
        # Calculate preview levels
        initial_risk = ATR_MULTIPLIER * atr_val if atr_val > 0 else price * 0.04
        if side == "LONG":
            preview_sl = price - initial_risk
            preview_tp1 = price + (initial_risk * TP1_RR_RATIO)
        else:
            preview_sl = price + initial_risk
            preview_tp1 = price - (initial_risk * TP1_RR_RATIO)
        
        balance = state.get("portfolio_balance", 25000.0)
        order_size_usd = balance * POSITION_SIZE_PCT
        coin_qty = math.floor(order_size_usd / price) if price > 0 else 0
        
        keyboard = [
            [InlineKeyboardButton("✅ Opened", callback_data=f"open_{side}_{symbol}_{atr_val:.4f}"),
             InlineKeyboardButton("❌ Ignore", callback_data=f"ignore_{symbol}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = (
            f"🚨 **ACTION REQUIRED: {side} Signal** 🚨\n"
            f"Symbol: {symbol}\n"
            f"Score vs BTC: {score*100:.2f}%\n"
            f"Entry Price: {fmt_price(price)}\n"
            f"Stop Loss: {fmt_price(preview_sl)}\n"
            f"TP1 (1.5R): {fmt_price(preview_tp1)}\n"
            f"Order Size: ${order_size_usd:.2f} ({coin_qty} coins)"
        )
        await context.bot.send_message(chat_id=context.job.chat_id, text=text, reply_markup=reply_markup)
        
        # Mark as sent
        new_sent[sig_key] = datetime.now(timezone.utc).isoformat()
    
    # Keep only signals that are still active (returned by scanner this cycle)
    # Old signals that are no longer detected get cleared automatically
    active_sig_keys = {f"{s['symbol']}_{s['signal']}" for s in signals}
    for key in sent_signals:
        if key in active_sig_keys:
            new_sent[key] = sent_signals[key]
    
    state["sent_signals"] = new_sent
    save_state(state)

# ─── POSITION MONITOR (runs every 5 minutes) ─────────────────────────

async def position_monitor(context: ContextTypes.DEFAULT_TYPE):
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
        
        # Fetch 5m candles (last 2) for wick check + current ticker
        async def fetch_5m_candle(sym):
            try:
                ohlcv = await exchange.fetch_ohlcv(sym, "5m", limit=2)
                if ohlcv and len(ohlcv) >= 2:
                    return sym, ohlcv[-2]  # Last closed 5m candle [ts, o, h, l, c, v]
                return sym, None
            except Exception as e:
                logger.error(f"Error fetching 5m candle for {sym}: {e}")
                return sym, None
        
        # Fetch 1H candles for MACD exit check (only for tp1_hit positions)
        tp1_hit_symbols = list(set(p['symbol'] for p in positions if p.get('tp1_hit', False)))
        
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
        
        # Execute fetches in parallel
        candle_tasks = [fetch_5m_candle(sym) for sym in symbols]
        macd_tasks = [fetch_1h_macd(sym) for sym in tp1_hit_symbols]
        
        all_results = await asyncio.gather(*candle_tasks, *macd_tasks)
        
        candle_results = all_results[:len(candle_tasks)]
        macd_results = all_results[len(candle_tasks):]
        
        candles_5m = {sym: c for sym, c in candle_results if c is not None}
        macd_dfs = {sym: df for sym, df in macd_results if df is not None}
        
        tickers = await exchange.fetch_tickers(symbols)
        
        for p in positions:
            symbol = p['symbol']
            side = p.get('side', 'LONG')
            entry = p['entry_price']
            sl = p['current_sl']
            tp1 = p.get('tp1_price', 0)
            tp1_hit = p.get('tp1_hit', False)
            denial_count = p.get('denial_count', 0)
            
            ticker = tickers.get(symbol)
            if not ticker: continue
            
            current_price = ticker['last']
            
            # ── Check SL breach via 5m wick + current price ──
            breached = False
            
            if symbol in candles_5m:
                c = candles_5m[symbol]
                # c = [timestamp, open, high, low, close, volume]
                if side == "LONG" and c[3] <= sl:      # low <= SL
                    breached = True
                elif side == "SHORT" and c[2] >= sl:   # high >= SL
                    breached = True
            
            if not breached:
                if side == "LONG" and current_price <= sl: breached = True
                elif side == "SHORT" and current_price >= sl: breached = True
                
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
                continue
                
            # ── Check TP1 (only if not yet hit) ──
            if not tp1_hit and tp1 > 0:
                tp1_reached = False
                
                # Check 5m wick
                if symbol in candles_5m:
                    c = candles_5m[symbol]
                    if side == "LONG" and c[2] >= tp1:     # high >= TP1
                        tp1_reached = True
                    elif side == "SHORT" and c[3] <= tp1:  # low <= TP1
                        tp1_reached = True
                
                # Check current price
                if not tp1_reached:
                    if side == "LONG" and current_price >= tp1: tp1_reached = True
                    elif side == "SHORT" and current_price <= tp1: tp1_reached = True
                    
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
                    continue
            
            # ── MACD momentum exit (only for positions where TP1 already hit) ──
            if tp1_hit and symbol in macd_dfs and denial_count < 2:
                df = macd_dfs[symbol]
                macd_line = df.get('MACD_12_26_9')
                macd_signal_line = df.get('MACDs_12_26_9')
                
                if macd_line is not None and macd_signal_line is not None:
                    ml_curr = macd_line.iloc[-2]
                    ms_curr = macd_signal_line.iloc[-2]
                    ml_prev = macd_line.iloc[-3]
                    ms_prev = macd_signal_line.iloc[-3]
                    
                    if not any(pd.isna(x) for x in [ml_curr, ms_curr, ml_prev, ms_prev]):
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
            
            # Reset denial count if price is safely away from SL
            p['denial_count'] = 0
                
        save_state(state)
    finally:
        await exchange.close()

# ─── MAIN ─────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN environment variable is not set!")
        return

    async def post_init(application: Application):
        state = load_state()
        chat_id = state.get("chat_id")
        if chat_id:
            _register_jobs(application.job_queue, chat_id)
            logger.info(f"Resumed signal_scanner + position_monitor for chat_id {chat_id}")

    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("afk", afk))
    application.add_handler(CommandHandler("ready", ready))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Starting bot...")
    application.run_polling()

if __name__ == "__main__":
    main()
