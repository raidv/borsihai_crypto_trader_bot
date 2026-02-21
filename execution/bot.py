import asyncio
import logging
import os
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import ccxt.async_support as ccxt

from state_manager import load_state, save_state
from scanner import scan_market

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEBUG_RUN_IMMEDIATELY = False  # Set to False to disable the automatic run on startup

_has_run_once = False

# Note: You need to set TELEGRAM_TOKEN locally or in .env

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = load_state()
    state["chat_id"] = chat_id
    save_state(state)
    
    current_jobs = context.job_queue.get_jobs_by_name("market_monitor")
    for job in current_jobs:
        job.schedule_removal()
        
    context.job_queue.run_repeating(market_monitor, interval=60, first=0, chat_id=chat_id, name="market_monitor")
    await update.message.reply_text("Börsihai 2026 Crypto Assistant is online. Monitoring job registered.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    balance = state.get("portfolio_balance", 0.0)
    positions = state.get("active_positions", [])
    
    if not positions:
        msg = f"Balance: ${balance:.2f}\nNo active positions."
    else:
        msg = f"Balance: ${balance:.2f}\nActive Positions:\n"
        for p in positions:
            msg += f"- {p['symbol']} ({p.get('side', 'LONG')}): Entry ${p['entry_price']:.4f} | SL ${p['current_sl']:.4f}\n"
    await update.message.reply_text(msg)

async def afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["bot_status"] = "afk"
    
    positions = state.get("active_positions", [])
    if not positions:
        save_state(state)
        await update.message.reply_text("Bot is now AFK. No incoming signals.")
        return
        
    exchange = ccxt.binance()
    try:
        symbols = [p['symbol'] for p in positions]
        tickers = await exchange.fetch_tickers(symbols)
        msg = "AFK Mode Active. Signals paused.\n\nUpdate StockTrak with these safety levels:\n"
        
        for p in positions:
            ticker = tickers.get(p['symbol'])
            if not ticker: continue
            curr_price = ticker['last']
            
            # AFK TP is +10% from CURRENT price
            if p.get('side', 'LONG') == 'LONG':
                afk_tp = curr_price * 1.10
            else:
                afk_tp = curr_price * 0.90
                
            p['afk_tp'] = afk_tp
            msg += f"- {p['symbol']}: SL = ${p['current_sl']:.4f} | TP = ${afk_tp:.4f}\n"
            
        save_state(state)
        await update.message.reply_text(msg)
    finally:
        await exchange.close()

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🦈 *Börsihai 2026 Crypto Assistant*\n\n"
        "I am an algorithmic momentum bot tracking 100 Binance coins against USDT. "
        "I hunt for 15m EMA 20/50 crosses with RSI and 1h confluence.\n\n"
        "**Available Commands:**\n"
        "• `/status` - Check portfolio balance, floating P/L, and active trades.\n"
        "• `/afk` - Pause scanning signals and see hard TP/SL to enter into StockTrak for safety overnight.\n"
        "• `/ready` - Leave AFK mode and resume hunting for signals.\n"
        "• `/start` - Explicitly forces the 15m monitoring loop to register (you don't need to press this again after restarting).\n"
        "• `/help` - What you're looking at right now."
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def ready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["bot_status"] = "ready"
    save_state(state)
    await update.message.reply_text("Bot is Ready. Tracking signals.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    state = load_state()
    exchange = ccxt.binance()
    
    try:
        if data.startswith("open_"):
            # Format: open_LONG_BTC/USDT
            _, side, symbol = data.split("_", 2)
            positions = state.get("active_positions", [])
            
            if len(positions) >= 20:
                await query.edit_message_text(f"Cannot open {symbol}: Max 20 positions reached.")
                return
                
            ticker = await exchange.fetch_ticker(symbol)
            price = ticker['last']
            balance = state.get("portfolio_balance", 25000.0)
            allocated_capital = balance / 20.0
            
            if side == "LONG":
                sl = price * 0.96
            else:
                sl = price * 1.04
                
            positions.append({
                "symbol": symbol,
                "side": side,
                "entry_price": price,
                "allocated_capital": allocated_capital,
                "current_sl": sl,
                "timestamp": datetime.now(timezone.utc).timestamp(),
                "highest_profit_pct": 0.0,
                "denial_count": 0
            })
            state["active_positions"] = positions
            save_state(state)
            
            await query.edit_message_text(f"✅ Opened {side} on {symbol} at ${price:.4f}. Initial SL set to ${sl:.4f}")
            
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
                    alloc = p.get('allocated_capital', state.get('portfolio_balance', 25000.0) / 20)
                    
                    if pos_side == "LONG":
                        net_pct = (sl - entry) / entry * 100 - 0.2
                    else:
                        net_pct = (entry - sl) / entry * 100 - 0.2
                    
                    pnl = alloc * (net_pct / 100)
                    state['portfolio_balance'] = state.get('portfolio_balance', 25000.0) + pnl
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
            await query.edit_message_text(f"❌ Denied SL closure for {symbol}. Will repoll later if breached.")
            
        elif data.startswith("trail_"):
            # Format: trail_LONG_BTC/USDT_1.5
            parts = data.split("_", 3)
            side = parts[1]
            symbol = parts[2]
            new_sl_pct = float(parts[3])
            
            positions = state.get("active_positions", [])
            for p in positions:
                if p['symbol'] == symbol:
                    entry = p['entry_price']
                    if side == "LONG":
                        new_target = entry * (1 + new_sl_pct/100)
                        if new_target > p['current_sl']:
                            p['current_sl'] = new_target
                    else:
                        new_target = entry * (1 - new_sl_pct/100)
                        if new_target < p['current_sl'] or p['current_sl'] == 0:
                            p['current_sl'] = new_target
                            
            state["active_positions"] = positions
            save_state(state)
            await query.edit_message_text(f"✅ Trailing SL applied for {symbol} at net +{new_sl_pct}%")
            
        elif data.startswith("trailignore_"):
            await query.edit_message_text("❌ Ignored trailing SL suggestion.")

    except Exception as e:
        logger.error(f"Error handling button: {e}")
    finally:
        await exchange.close()

async def market_monitor(context: ContextTypes.DEFAULT_TYPE):
    global _has_run_once
    now = datetime.now(timezone.utc)
    
    force_run = False
    if DEBUG_RUN_IMMEDIATELY and not _has_run_once:
        force_run = True
        _has_run_once = True
        logger.info("Forcing initial debug market scan...")
        
    # Runs every 1 min. Only execute exact logic on intervals of 15 min + 1 min (01, 16, 31, 46)
    if not force_run and now.minute % 15 != 1:
        return
        
    logger.info(f"Running 15m cycle at {now.strftime('%H:%M:%S')}")
    state = load_state()
    
    # 1. Scanner for new signals
    if state.get("bot_status") == "ready":
        signals = await scan_market()
        for sig in signals:
            symbol = sig['symbol']
            side = sig['signal']
            score = sig['score']
            price = sig['price']
            
            keyboard = [
                [InlineKeyboardButton("✅ Opened", callback_data=f"open_{side}_{symbol}"),
                 InlineKeyboardButton("❌ Ignore", callback_data=f"ignore_{symbol}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = f"🚨 **{side} Signal** 🚨\nSymbol: {symbol}\nScore: {score*100:.2f}%\nPrice: ${price:.4f}"
            await context.bot.send_message(chat_id=context.job.chat_id, text=text, reply_markup=reply_markup)
            
    # 2. Check active positions
    positions = state.get("active_positions", [])
    if not positions:
        return
        
    exchange = ccxt.binance()
    try:
        symbols = [p['symbol'] for p in positions]
        
        # Parallel fetch 15m candles to check SL breach accurately
        async def get_latest_candle(sym):
            try:
                ohlcv = await exchange.fetch_ohlcv(sym, "15m", limit=2)
                if ohlcv and len(ohlcv) >= 2:
                    return sym, ohlcv[-2] # Last closed candle
                return sym, None
            except Exception:
                return sym, None
                
        tasks = [get_latest_candle(sym) for sym in symbols]
        results = await asyncio.gather(*tasks)
        candles = {sym: candle for sym, candle in results if candle}
        
        tickers = await exchange.fetch_tickers(symbols)
        
        for p in positions:
            symbol = p['symbol']
            side = p.get('side', 'LONG')
            entry = p['entry_price']
            sl = p['current_sl']
            denial_count = p.get('denial_count', 0)
            
            ticker = tickers.get(symbol)
            if not ticker: continue
            
            current_price = ticker['last']
            
            # SL Breach Check
            breached = False
            if symbol in candles:
                c = candles[symbol]
                # c = [timestamp, open, high, low, close, volume]
                if side == "LONG" and c[3] <= sl:
                    breached = True
                elif side == "SHORT" and c[2] >= sl:
                    breached = True
            
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
                        text=f"⚠️ SL Breach detected for {symbol} at ${sl:.4f}. Did you close it in StockTrak?",
                        reply_markup=reply_markup
                    )
                continue # Skip trailing calculations if breached
            
            # Trailing Stop Calculation
            # Calculate net profit (deducting 0.2% fee)
            if side == "LONG":
                gross_pct = (current_price - entry) / entry * 100
            else:
                gross_pct = (entry - current_price) / entry * 100
                
            net_pct = gross_pct - 0.2
            max_profit = max(p.get('highest_profit_pct', 0), net_pct)
            p['highest_profit_pct'] = max_profit
            
            # Reset denial count if price recovered above SL
            p['denial_count'] = 0
            
            # Trailing Logic
            suggestion = None
            suggested_pct = None
            
            if max_profit >= 3.0:
                # Break-even is +0.2% to cover fees, or literally +0.2% of entry
                # The rule: SL to Entry * 1.002
                expected_sl_pct = 0.2
                
                if max_profit >= 6.0:
                    # Next trail at +6% profit -> SL to +3%
                    # Continuous: every further 3% gain -> SL up by 2.5%
                    steps = int((max_profit - 6.0) // 3.0)
                    if steps < 0:
                        expected_sl_pct = 3.0
                    else:
                        expected_sl_pct = 3.0 + (steps + 1) * 2.5
                
                if side == "LONG":
                    suggested_sl_price = entry * (1 + expected_sl_pct / 100)
                    if suggested_sl_price > sl: # Better than current
                        suggestion = suggested_sl_price
                        suggested_pct = expected_sl_pct
                else:
                    suggested_sl_price = entry * (1 - expected_sl_pct / 100)
                    if suggested_sl_price < sl or sl == 0:
                        suggestion = suggested_sl_price
                        suggested_pct = expected_sl_pct
                        
            if suggestion:
                keyboard = [
                    [InlineKeyboardButton("✅ Applied", callback_data=f"trail_{side}_{symbol}_{suggested_pct}"),
                     InlineKeyboardButton("❌ Ignore", callback_data=f"trailignore_{symbol}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_message(
                    chat_id=context.job.chat_id,
                    text=f"📈 {symbol} is up net +{net_pct:.2f}%. Move SL to +{suggested_pct}% (${suggestion:.4f})?",
                    reply_markup=reply_markup
                )
                
        save_state(state)
    finally:
        await exchange.close()

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

    # To avoid missing updates if closed
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
