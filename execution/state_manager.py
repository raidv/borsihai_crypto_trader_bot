import json
import os

STATE_FILE = "state.json"

def load_state():
    if not os.path.exists(STATE_FILE):
        default_state = {
            "portfolio_balance": 25000.0,
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
