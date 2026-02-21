import asyncio
import os
import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from state_manager import load_state

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

async def send_mock_signal():
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_TOKEN not set in .env")
        return

    state = load_state()
    chat_id = state.get("chat_id")
    if not chat_id:
        print("Error: chat_id not found in state.json. Have you sent /start to the bot yet?")
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    
    symbol = "BTC/USDT"
    side = "LONG"
    score = 0.99
    price = 65000.00
    
    keyboard = [
        [InlineKeyboardButton("✅ Opened", callback_data=f"open_{side}_{symbol}"),
         InlineKeyboardButton("❌ Ignore", callback_data=f"ignore_{symbol}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"🚨 **[TEST] {side} Signal** 🚨\nSymbol: {symbol}\nScore: {score*100:.2f}%\nPrice: ${price:.4f}"
    
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    print("Mock signal sent successfully! Check your Telegram.")

if __name__ == "__main__":
    asyncio.run(send_mock_signal())
