#!/bin/bash
set -e

cd /home/pi/borsihai_crypto_trader_bot

echo "🔄 Checking for updates..."
if git fetch --all; then
  git reset --hard origin/main
else
  echo "⚠️ Update failed (no network/DNS?). Starting existing version..."
fi

echo "🐍 Starting HaiBot26..."
source venv/bin/activate
exec python execution/bot.py