"""Börsihai 2026 Swing Assistant — Main entrypoint and scheduled jobs."""
import logging
import math
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import (
    TELEGRAM_TOKEN, MAX_POSITIONS, POSITION_SIZE_PCT,
    ATR_MULTIPLIER, TP1_RR_RATIO, fmt_price, setup_logging,
    DEFAULT_TIMEFRAME, TIMEFRAME_PAIRINGS,
)
from state_manager import load_state, save_state
from scanner import scan_market
from telegram_handlers import start, status, afk, ready, help_command, button_handler, scan, restart
from position_manager import position_monitor

setup_logging()
logger = logging.getLogger("Bot")

DEBUG_RUN_IMMEDIATELY = False
_has_run_once = False


# ─── JOB REGISTRATION ────────────────────────────────────────────────

def register_jobs(context_or_jq, chat_id, entry_tf: str = None):
    """Register both the signal_scanner and position_monitor jobs."""
    jq = context_or_jq if hasattr(context_or_jq, 'run_repeating') else context_or_jq.job_queue

    if entry_tf is None:
        entry_tf = DEFAULT_TIMEFRAME

    pairing = TIMEFRAME_PAIRINGS.get(entry_tf, TIMEFRAME_PAIRINGS[DEFAULT_TIMEFRAME])
    scan_interval_sec = pairing["scan_interval"] * 60  # convert minutes → seconds
    monitor_interval_sec = 60  # position monitor always runs every 60s (filters internally)

    # Remove existing jobs
    for name in ["signal_scanner", "position_monitor"]:
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()

    jq.run_repeating(signal_scanner, interval=scan_interval_sec, first=0, chat_id=chat_id, name="signal_scanner")
    jq.run_repeating(position_monitor, interval=monitor_interval_sec, first=30, chat_id=chat_id, name="position_monitor")


# ─── SIGNAL SCANNER (runs every hour) ─────────────────────────────────

async def signal_scanner(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled market scan job. Checks for new entry signals."""
    global _has_run_once
    now = datetime.now(timezone.utc)

    state = load_state()
    entry_tf = state.get("timeframe", DEFAULT_TIMEFRAME)
    pairing = TIMEFRAME_PAIRINGS.get(entry_tf, TIMEFRAME_PAIRINGS[DEFAULT_TIMEFRAME])
    scan_interval_min = pairing["scan_interval"]

    force_run = False
    if not _has_run_once:
        force_run = True
        _has_run_once = True
        logger.info(f"Forcing initial market scan on startup (TF={entry_tf})...")

    # Run at the correct minute boundary for the configured timeframe
    if not force_run and (now.minute % scan_interval_min) != 0:
        return

    logger.info(f"Running {entry_tf} signal scan at {now.strftime('%H:%M:%S')}")

    if state.get("bot_status") != "ready":
        return

    signals = await scan_market(entry_tf=entry_tf)

    # Unpack return format: {signals: [...], metadata: {...}}
    scan_result = signals if isinstance(signals, dict) else {"signals": signals or [], "metadata": {}}
    signal_list = scan_result.get("signals", [])
    metadata = scan_result.get("metadata", {})
    pairs_scanned = metadata.get("pairs_scanned", 0)
    active_tf = metadata.get("entry_tf", entry_tf)
    tf_label = f"[{active_tf.upper()}]"

    if not signal_list:
        await context.bot.send_message(
            chat_id=context.job.chat_id,
            text=(
                f"✅ Heartbeat {tf_label}: scan ran at {now.strftime('%H:%M UTC')} — no signals found.\n"
                f"Pairs scanned: {pairs_scanned}"
            )
        )
        return

    # Deduplicate: check which signals were already sent
    sent_signals = state.get("sent_signals", {})
    active_positions = state.get("active_positions", [])
    open_positions_set = {f"{p['symbol']}_{p.get('side', 'LONG')}" for p in active_positions}

    new_sent = {}
    sent_pairs = []
    discarded_pairs = []

    for sig in signal_list:
        symbol = sig['symbol']
        side = sig['signal']
        score = sig.get('score', 0)
        score_display = sig.get('score_display', f"Score: {score}/100")

        if f"{symbol}_{side}" in open_positions_set:
            logger.info(f"Skipping {symbol} ({side}): already have open position in this direction.")
            discarded_pairs.append(f"{symbol} ({side}) — {score}/100 [open pos]")
            continue
        price = sig['price']
        atr_val = sig.get('atr', 0)

        sig_key = f"{symbol}_{side}"
        if sig_key in sent_signals:
            logger.info(f"Skipping duplicate signal: {sig_key}")
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

        balance = state.get("portfolio_balance", 25000.0)
        order_size_usd = balance * POSITION_SIZE_PCT
        coin_qty = math.floor(order_size_usd / price) if price > 0 else 0

        path = sig.get('path', 'TA')
        path_label = "[TREND]" if path == "TA" else "[COUNTERTREND]"

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
        await context.bot.send_message(chat_id=context.job.chat_id, text=text, reply_markup=reply_markup)

        new_sent[sig_key] = datetime.now(timezone.utc).isoformat()
        sent_pairs.append(f"{symbol} ({side}) — {score}/100")

    # Keep only signals that are still active
    active_sig_keys = {f"{s['symbol']}_{s['signal']}" for s in signal_list}
    for key in sent_signals:
        if key in active_sig_keys:
            new_sent[key] = sent_signals[key]

    state["sent_signals"] = new_sent
    save_state(state)

    # Send scan summary
    summary = f"📋 **Scan Summary {tf_label}** ({now.strftime('%H:%M UTC')})\n"
    summary += f"Pairs scanned: {pairs_scanned}\n"
    summary += f"Alerts generated: {len(signal_list)} | Sent: {len(sent_pairs)} | Discarded: {len(discarded_pairs)}\n"
    if sent_pairs:
        summary += "\n**Sent:**\n" + "\n".join(f"  • {p}" for p in sent_pairs)
    if discarded_pairs:
        summary += "\n**Discarded:**\n" + "\n".join(f"  • {p}" for p in discarded_pairs)
    await context.bot.send_message(chat_id=context.job.chat_id, text=summary)


# ─── MAIN ─────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN environment variable is not set!")
        return

    async def post_init(application: Application):
        state = load_state()
        chat_id = state.get("chat_id")
        entry_tf = state.get("timeframe", DEFAULT_TIMEFRAME)
        pairing = TIMEFRAME_PAIRINGS.get(entry_tf, TIMEFRAME_PAIRINGS[DEFAULT_TIMEFRAME])
        trend_tf = pairing["trend"]
        if chat_id:
            register_jobs(application.job_queue, chat_id, entry_tf)
            msg = (
                f"🟢 **Börsihai Bot Started**\n"
                f"Service initiated. Active timeframe: **{entry_tf.upper()}** (Trend: {trend_tf.upper()})\n"
                f"Type /timeframe \u003cvalue\u003e to change (e.g. /timeframe 4h)"
            )
            await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
            logger.info(f"Started with TF={entry_tf}. Resumed jobs for chat_id {chat_id}")
        else:
            logger.warning("No chat_id found in state. User must send /start to activate.")

    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    async def error_handler(update, context):
        logger.exception("Unhandled exception occurred", exc_info=context.error)
        try:
            state = load_state()
            chat_id = state.get("chat_id")
            if update and getattr(update, "callback_query", None):
                await update.callback_query.answer(
                    text=f"⚠️ Bot error occurred: {context.error}",
                    show_alert=True
                )
            elif chat_id:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Bot error occurred:\n{context.error}"
                )
        except Exception:
            pass

    from telegram_handlers import clean, close_position, manual_long, manual_short, update_sl, timeframe_command

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("afk", afk))
    application.add_handler(CommandHandler("ready", ready))
    application.add_handler(CommandHandler("scan", scan))
    application.add_handler(CommandHandler("restart", restart))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clean", clean))
    application.add_handler(CommandHandler("ignore", clean))
    application.add_handler(CommandHandler("close", close_position))
    application.add_handler(CommandHandler("long", manual_long))
    application.add_handler(CommandHandler("short", manual_short))
    application.add_handler(CommandHandler("sl", update_sl))
    application.add_handler(CommandHandler("timeframe", timeframe_command))
    application.add_handler(CallbackQueryHandler(button_handler))

    application.add_error_handler(error_handler)

    logger.info("Starting bot...")
    application.run_polling()


if __name__ == "__main__":
    main()
