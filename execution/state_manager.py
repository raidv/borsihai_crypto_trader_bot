import json
import os
from datetime import datetime, timezone

STATE_FILE = "state.json"
TRADE_LOG_FILE = "trade_log.json"

def log_trade(action, symbol, side, price, sl, timestamp, pnl=None):
    entry = {
        "action": action,
        "symbol": symbol,
        "side": side,
        "price": price,
        "sl": sl,
        "timestamp": datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if isinstance(timestamp, float) else timestamp,
        "pnl": pnl
    }
    log_data = []
    if os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE, "r") as f:
            try:
                log_data = json.load(f)
            except json.JSONDecodeError:
                pass
    log_data.append(entry)
    with open(TRADE_LOG_FILE, "w") as f:
        json.dump(log_data, f, indent=4)

def load_state():
    if not os.path.exists(STATE_FILE):
        default_state = {
            "portfolio_balance": 25000.0,
            "available_cash": 25000.0,
            "tied_capital": 0.0,
            "bot_status": "ready",
            "active_positions": []
        }
        save_state(default_state)
        return default_state
    
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):
    # Atomic write to prevent corruption
    tmp_file = STATE_FILE + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump(state, f, indent=4)
    os.replace(tmp_file, STATE_FILE)
