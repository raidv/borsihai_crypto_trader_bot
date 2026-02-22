import asyncio
import logging
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

load_dotenv()  # Load environment variables from .env file

logging.basicConfig(level=logging.INFO)
# Silence httpx logs to prevent HTTP Request URIs (which contain the token) from printing
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("Bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEBUG_RUN_IMMEDIATELY = False  # Set to True to force an immediate scan on startup

MAX_POSITIONS = 10
POSITION_SIZE_PCT = 0.10  # 10% of portfolio per position
ATR_MULTIPLIER = 2.0      # SL = Entry +/- (ATR * 2.0)
TP1_RR_RATIO = 1.5        # TP1 at 1.5x risk

_has_run_once = False

# ─── COMMAND HANDLERS ──────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = load_state()
    state["chat_id"] = chat_id
    save_state(state)
    
    current_jobs = context.job_queue.get_jobs_by_name("market_monitor")
    for job in current_jobs:
        job.schedule_removal()
        
    context.job_queue.run_repeating(market_monitor, interval=60, first=0, chat_id=chat_id, name="market_monitor")
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
            msg += f"  Entry: ${p['entry_price']:.4f} | SL: ${p['current_sl']:.4f}\n"
            msg += f"  TP1: ${p.get('tp1_price', 0):.4f} [{tp1_status}]\n"
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
            
            # Safety SL: 4% away from current market price
            if p.get('side', 'LONG') == 'LONG':
                afk_sl = curr_price * 0.96
                afk_tp = curr_price * 1.10
            else:
                afk_sl = curr_price * 1.04
                afk_tp = curr_price * 0.90
                
            msg += f"\n- **{p['symbol']}** ({p.get('side', 'LONG')}):\n"
            msg += f"  Safety SL: ${afk_sl:.4f}\n"
            msg += f"  Moon-shot TP: ${afk_tp:.4f}\n"
            
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
                f"Entry: ${price:.4f}\n"
                f"SL: ${sl:.4f}\n"
                f"TP1: ${tp1:.4f}\n"
                f"Size: ${allocated_capital:.2f}"
            )
            
        elif data.startswith("ignore_"):
            await query.edit_message_text("❌ Ignored signal.")
            
        elif data.startswith("slclosed_"):
            # Format: slclosed_BTC/USDT
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
            await query.edit_message_text(f"❌ Denied closure for {symbol}. Will re-check on next candle.")
            
        elif data.startswith("halfclose_"):
            # Format: halfclose_BTC/USDT
            _, symbol = data.split("_", 1)
            positions = state.get("active_positions", [])
            
            for p in positions:
                if p['symbol'] == symbol and not p.get('tp1_hit', False):
                    p['tp1_hit'] = True
                    entry = p['entry_price']
                    side = p.get('side', 'LONG')
                    alloc = p.get('allocated_capital', 0)
                    half_alloc = alloc / 2.0
                    
                    # Calculate PnL on the closed half
                    tp1 = p.get('tp1_price', entry)
                    if side == "LONG":
                        net_pct = (tp1 - entry) / entry * 100 - 0.2
                        be_sl = entry * 1.002  # Break-even + fees
                    else:
                        net_pct = (entry - tp1) / entry * 100 - 0.2
                        be_sl = entry * 0.998
                    
                    pnl = half_alloc * (net_pct / 100)
                    
                    # Update portfolio: release half the capital + profit
                    state['portfolio_balance'] = state.get('portfolio_balance', 25000.0) + pnl
                    state['available_cash'] = state.get('available_cash', 0) + half_alloc + pnl
                    state['tied_capital'] = max(0.0, state.get('tied_capital', 0.0) - half_alloc)
                    
                    # Halve remaining allocation and move SL to break-even
                    p['allocated_capital'] = half_alloc
                    p['current_sl'] = be_sl
                    
                    log_trade("PARTIAL_CLOSE", symbol, side, entry, tp1, datetime.now(timezone.utc).timestamp(), pnl)
                    
            state["active_positions"] = positions
            save_state(state)
            await query.edit_message_text(
                f"⚡ TP1 half-close confirmed for {symbol}.\n"
                f"SL moved to break-even. Remaining 50% is running."
            )

    except Exception as e:
        logger.error(f"Error handling button: {e}")
    finally:
        await exchange.close()

# ─── MARKET MONITOR ───────────────────────────────────────────────────

async def market_monitor(context: ContextTypes.DEFAULT_TYPE):
    global _has_run_once
    now = datetime.now(timezone.utc)
    
    force_run = False
    if DEBUG_RUN_IMMEDIATELY and not _has_run_once:
        force_run = True
        _has_run_once = True
        logger.info("Forcing initial debug market scan...")
        
    # Runs every 1 min. Execute on 15m intervals (01, 16, 31, 46)
    if not force_run and now.minute % 15 != 1:
        return
        
    logger.info(f"Running 15m monitoring cycle at {now.strftime('%H:%M:%S')}")
    state = load_state()
    
    # ─── 1. Scanner for new entry signals ──────────────────────────
    if state.get("bot_status") == "ready":
        signals = await scan_market()
        for sig in signals:
            symbol = sig['symbol']
            side = sig['signal']
            score = sig.get('score', 0)
            price = sig['price']
            atr_val = sig.get('atr', 0)
            
            # Calculate preview levels
            initial_risk = ATR_MULTIPLIER * atr_val if atr_val > 0 else price * 0.04
            if side == "LONG":
                preview_sl = price - initial_risk
                preview_tp1 = price + (initial_risk * TP1_RR_RATIO)
            else:
                preview_sl = price + initial_risk
                preview_tp1 = price - (initial_risk * TP1_RR_RATIO)
            
            balance = state.get("portfolio_balance", 25000.0)
            order_size = balance * POSITION_SIZE_PCT
            
            # Pass ATR in callback data so open handler can use it
            keyboard = [
                [InlineKeyboardButton("✅ Opened", callback_data=f"open_{side}_{symbol}_{atr_val:.4f}"),
                 InlineKeyboardButton("❌ Ignore", callback_data=f"ignore_{symbol}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            text = (
                f"🚨 **ACTION REQUIRED: {side} Signal** 🚨\n"
                f"Symbol: {symbol}\n"
                f"Score vs BTC: {score*100:.2f}%\n"
                f"Entry Price: ${price:.4f}\n"
                f"Stop Loss: ${preview_sl:.4f}\n"
                f"TP1 (1.5R): ${preview_tp1:.4f}\n"
                f"Order Size: ${order_size:.2f}"
            )
            await context.bot.send_message(chat_id=context.job.chat_id, text=text, reply_markup=reply_markup)
            
    # ─── 2. Monitor active positions ───────────────────────────────
    positions = state.get("active_positions", [])
    if not positions:
        return
        
    exchange = ccxt.binance()
    try:
        symbols = list(set(p['symbol'] for p in positions))
        
        # Fetch 1H candles for SL breach check + MACD early exit
        async def fetch_1h_data(sym):
            try:
                ohlcv = await exchange.fetch_ohlcv(sym, "1h", limit=50)
                if ohlcv and len(ohlcv) >= 30:
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df.ta.macd(fast=12, slow=26, signal=9, append=True)
                    return sym, df
                return sym, None
            except Exception as e:
                logger.error(f"Error fetching monitor data for {sym}: {e}")
                return sym, None
                
        tasks = [fetch_1h_data(sym) for sym in symbols]
        results = await asyncio.gather(*tasks)
        candles_dfs = {sym: df for sym, df in results if df is not None}
        
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
            
            # ── SL Breach Check ──
            breached = False
            macd_exit_warning = None
            
            if symbol in candles_dfs:
                df = candles_dfs[symbol]
                c_low = df['low'].iloc[-2]
                c_high = df['high'].iloc[-2]
                
                if side == "LONG" and c_low <= sl:
                    breached = True
                elif side == "SHORT" and c_high >= sl:
                    breached = True
                    
                # ── MACD Early Exit Check ──
                macd_line = df.get('MACD_12_26_9')
                macd_signal_line = df.get('MACDs_12_26_9')
                
                if macd_line is not None and macd_signal_line is not None:
                    ml_curr = macd_line.iloc[-2]
                    ms_curr = macd_signal_line.iloc[-2]
                    ml_prev = macd_line.iloc[-3]
                    ms_prev = macd_signal_line.iloc[-3]
                    
                    if not any(pd.isna(x) for x in [ml_curr, ms_curr, ml_prev, ms_prev]):
                        if side == "LONG":
                            # MACD crosses below signal = bearish reversal
                            if ml_prev >= ms_prev and ml_curr < ms_curr:
                                macd_exit_warning = "MACD bearish cross on 1H"
                        elif side == "SHORT":
                            # MACD crosses above signal = bullish reversal
                            if ml_prev <= ms_prev and ml_curr > ms_curr:
                                macd_exit_warning = "MACD bullish cross on 1H"
            
            # Fallback current price check
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
                        text=f"🚨 **ACTION REQUIRED: SL Breach** for {symbol} at ${sl:.4f}. Did you close it in StockTrak?",
                        reply_markup=reply_markup
                    )
                continue
                
            # ── TP1 Check ──
            if not tp1_hit and tp1 > 0:
                tp1_reached = False
                if side == "LONG" and current_price >= tp1:
                    tp1_reached = True
                elif side == "SHORT" and current_price <= tp1:
                    tp1_reached = True
                    
                if tp1_reached:
                    keyboard = [
                        [InlineKeyboardButton("✅ Half-Closed", callback_data=f"halfclose_{symbol}"),
                         InlineKeyboardButton("❌ Ignore", callback_data=f"slopen_{symbol}")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await context.bot.send_message(
                        chat_id=context.job.chat_id,
                        text=(
                            f"⚡ **UPDATE: TP1 Hit** for {symbol}!\n"
                            f"Close 50% of your position now.\n"
                            f"SL will move to break-even after confirmation."
                        ),
                        reply_markup=reply_markup
                    )
                    continue
            
            # ── MACD Early Exit Warning ──
            if macd_exit_warning and denial_count < 2:
                keyboard = [
                    [InlineKeyboardButton("✅ Closed", callback_data=f"slclosed_{symbol}"),
                     InlineKeyboardButton("❌ Ignore", callback_data=f"slopen_{symbol}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(
                    chat_id=context.job.chat_id,
                    text=f"🚨 **ACTION REQUIRED: Early Exit** for {symbol}!\nReason: {macd_exit_warning}.\nClose position?",
                    reply_markup=reply_markup
                )
            
            # Reset denial count if price recovered
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
            application.job_queue.run_repeating(
                market_monitor, 
                interval=60, 
                first=10, 
                chat_id=chat_id, 
                name="market_monitor"
            )
            logger.info(f"Resumed market_monitor for chat_id {chat_id}")

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
