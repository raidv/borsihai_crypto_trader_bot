# Börsihai Crypto Swing Trader 🦈

Börsihai is a professional-grade cryptocurrency swing trading assistant designed to run 24/7 (e.g., on a Raspberry Pi). It monitors the market using a multi-timeframe strategy (4H/1H) and sends actionable, scored trading signals directly to Telegram.

## Strategy Overview

## Strategy Overview

The bot implements a robust **Hybrid** swing strategy:
1. **Trend Filter (4H Regime):** Determines the baseline direction. Long if price > 200 EMA; Short if < 200 EMA.
2. **Dual-Path Entry Trigger (1H):** 
   - **Path A (Trend-Aligned - TA):** Matches 4H regime, simply requires EMA 20/50 alignment, a new MACD cross, and 2 bars of histogram confirmation.
   - **Path B (Countertrend - CT):** Triggers against the 4H regime ONLY under extreme constraints (explosive histogram momentum percentile, 3 bars holding, breakout of 12-bar range, high relative volume).
3. **Unified Pillar Scoring:** Signals are scored from 0-100 across 4 pillars:
   - **Momentum (40pts):** Histogram persistence, Delta percentile, Magnitude percentile.
   - **Structure (25pts):** EMA alignment, breakout status, anti-chase penalty.
   - **Cleanliness (20pts):** Relative volume percentile, candlestick wick safety.
   - **Context (15pts):** Alignment with 4H regime, directional BTC relative strength.
4. **Dynamic Risk Management:** Stop Loss is set at ~ATR-based spacing. Take Profit 1 (TP1) is calculated at 1.5R (1.5x risk).
5. **Position Monitoring:** Tracks active positions every 5 minutes, notifying you to move SL to break-even when TP1 is hit, or to fully exit if a reverse MACD cross or **CT Momentum Fade** occurs.

## Key Features

- **Telegram Interface:** Full control via Telegram (`/status`, `/scan`, `/afk`, `/ready`, `/restart`).
- **Composite Scoring:** Visual bar charts in Telegram for signal strength evaluation.
- **Smart Deduplication:** Prevents spamming the same signal multiple times.
- **AFK Mode (`/afk`):** Pauses new signals and gives you "safety net" SL/TP levels to set manually while you sleep or are away.
- **Multi-Position Tracking:** Tracks up to 10 open positions simultaneously, managing capital allocation and simulated PnL.
- **Robust Persistence:** Atomic JSON state saving ensures your portfolio data survives reboots.

## Project Structure

- `execution/bot.py`: Main entrypoint; registers Telegram handlers and the hourly scan cronjob.
- `execution/config.py`: Centralized constants, logging setup, and price formatting.
- `execution/scanner.py`: The core market analysis logic, indicators, and composite scoring.
- `execution/state_manager.py`: Atomic read/write operations for `state.json` and `trade_log.json`.
- `execution/telegram_handlers.py`: Command and interactive button logic.
- `execution/position_manager.py`: The 5-minute loop that monitors SL breaches, TP hits, and MACD momentum exits.

## Installation & Setup

### 1. Prerequisites
- Linux OS (Ubuntu, Debian, Raspberry Pi OS)
- Python 3.11+
- A Telegram Bot Token from [@BotFather](https://t.me/botfather)

### 2. Basic Setup
```bash
git clone https://github.com/raidv/borsihai_crypto_trader_bot.git
cd borsihai_crypto_trader_bot

# Run the setup script (installs python3.12, venv, dependencies)
./setup.sh

# Configure your environment variables
cp .env.example .env
nano .env # Paste your TELEGRAM_TOKEN
```

### 3. Systemd Service Setup (Recommended)
To ensure the bot starts automatically on boot and recovers from crashes, install it as a systemd service:

> [!CAUTION]
> The setup script uses `sudo` to write to `/etc/systemd/system/`. Review `setup_service.sh` if you prefer manual configuration.

```bash
chmod +x setup_service.sh
./setup_service.sh
```

### 4. Activating the Bot
Once the service is running, open Telegram and message your bot:
1. Send any command (e.g., `/help`) — the bot will automatically capture and register your Chat ID.
2. Send `/start` — this activates the hourly signal scanner and the 5-minute position monitor.

## Telegram Commands

- `/start` - Activate monitoring loops.
- `/status` - View portfolio equity, available cash, and detailed status (Entry, SL, TP1) of all open positions.
- `/afk` - Pause scanning and receive safety stop-loss/take-profit levels for all open positions.
- `/ready` - Resume active signal scanning.
- `/scan` - Force an immediate market scan and receive a detailed summary (Pairs scanned, Alerts sent, Alerts discarded).
- `/restart` - Restarts the underlying systemd service (requires sudo privileges configured for the bot user).
- `/help` - Show the help manual.

## Development & Testing

Börsihai includes a comprehensive unit test suite covering state management, market scanning, and bot logic.

```bash
source venv/bin/activate
cd execution
python -m pytest tests/ -v
```
