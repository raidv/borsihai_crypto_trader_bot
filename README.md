# Börsihai 2026 Crypto Assistant

A Python-based Telegram bot built for the manually-traded "Börsihai 2026" stock market competition on StockTrak. 

This bot monitors the top 100 cryptocurrencies 24/7 on Binance and sends actionable entry alerts directly to Telegram based on a customized momentum algorithmic strategy (EMA crossovers + RSI + Timeframe Confluence) while rigidly managing Risk.

## Algorithm Overview
- **Timeframes**: Evaluates 15m and 1h Binance OHLCV data.
- **Entry Strategy**: Matches exact EMA20/EMA50 crosses with trailing RSI between 55-65 (Long) or 35-45 (Short) on the `15m` timeframe. Signals must also have confluence on the `1h` timeframe.
- **Priority**: A tie-breaker calculates exactly how much the coin is outperforming/underperforming BTC over the last 4 hours (16 candles) and ranks by the `pairs.txt` order (Market Cap).
- **Risk Management**: Strictly limits to a max of 20 positions sized equally. Hard-coded rules trail Stop Losses mathematically to guarantee locked profits automatically.

## Files Structure
- `execution/bot.py` - The async Telegram Bot and main polling orchestrator.
- `execution/scanner.py` - The heavy-lifting market intelligence script (`pandas`/`pandas_ta`).
- `execution/pairs.txt` - The target list of symbols formatted for ccxt (e.g., `BTC/USDT`). Reorder this file to change symbol Market Cap Priority tie-breaking.
- `state.json` - Saves portfolio balance, historical denial rates, and active trades dynamically so that restarting your bot safely resumes context.

## Prerequisites
- **Linux / WSL**
- **Python 3.12** or newer
- **A valid Telegram Bot Token** (Acquired via [@BotFather](https://t.me/botfather))

## Auto-Setup
To effortlessly set up this bot on a new clean Linux, Ubuntu, or Windows WSL instance, run the interactive installer:

```bash
chmod +x setup.sh
./setup.sh
```
*(This automatically safely validates the OS, installs Python 3.12, builds the `venv`, and pulls all dependencies.)*

## How to Run

1. **Export your Token**: Ensure your Telegram bot token is stored to an environment variable in your terminal:
   ```bash
   export TELEGRAM_TOKEN='YOUR_BOT_TOKEN_FROM_BOTFATHER'
   ```
2. **Launch the Bot**:
   ```bash
   ./run.sh
   ```
3. **Connect in Telegram**: Send `/start` to your Bot in Telegram. This tells the bot what your personal User ID is and enables the 15-minute scheduled polling scanners directly to you.

## Bot Commands
- `/start`: Mounts your user ID to trigger the background scanning loop.
- `/status`: Check your floating P/L, portfolio balance, and dynamically see all active Open Trades & their hard SL thresholds.
- `/afk`: Halts scanners from proposing *new* pairs and explicitly calculates a quick `+10%` Take Profit point (TP) for every open position so you can set "Safety Nets" on StockTrak when you go to sleep.
- `/ready`: Drops the bot out of AFK mode and resumes sending momentum pair signals.
