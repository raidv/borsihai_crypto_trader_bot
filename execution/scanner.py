import asyncio
import ccxt.async_support as ccxt
import pandas as pd
import pandas_ta as ta
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Scanner")

async def fetch_ohlcv(exchange, symbol, timeframe, limit=100):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        logger.error(f"Error fetching {symbol} {timeframe}: {e}")
        return None

async def check_pair_15m(exchange, symbol):
    df = await fetch_ohlcv(exchange, symbol, "15m", limit=100)
    if df is None or len(df) < 51: return None
    
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.rsi(length=14, append=True)
    
    ema20 = df['EMA_20']
    ema50 = df['EMA_50']
    rsi = df['RSI_14']
    
    curr = -2  # Wait! The current active candle (incomplete) is -1, the last CLOSED candle is -2.
    prev = -3  # Previous closed candle is -3.
    
    # Exact cross long
    long_cross = ema20.iloc[prev] <= ema50.iloc[prev] and ema20.iloc[curr] > ema50.iloc[curr]
    long_rsi = 55 <= rsi.iloc[curr] <= 65
    
    # Exact cross short
    short_cross = ema20.iloc[prev] >= ema50.iloc[prev] and ema20.iloc[curr] < ema50.iloc[curr]
    short_rsi = 35 <= rsi.iloc[curr] <= 45
    
    signal = None
    if long_cross and long_rsi: signal = "LONG"
    elif short_cross and short_rsi: signal = "SHORT"
    
    if not signal: return None
    
    # Calculate relative strength vs BTC over the last 16 closed candles (4 hours)
    # the exact 16 candles diff: close.iloc[curr] (which is close[-2]) vs close.iloc[curr - 16] (which is close[-18])
    close = df['close']
    if len(close) > 20:
        base_idx = curr - 16
        coin_pct_change = (close.iloc[curr] - close.iloc[base_idx]) / close.iloc[base_idx]
    else:
        coin_pct_change = 0.0
        
    return {"symbol": symbol, "signal": signal, "coin_pct": coin_pct_change, "price": close.iloc[curr]}

async def check_pair_1h(exchange, symbol, signal):
    df = await fetch_ohlcv(exchange, symbol, "1h", limit=100)
    if df is None or len(df) < 51: return False
    
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.rsi(length=14, append=True)
    
    ema20 = df['EMA_20']
    ema50 = df['EMA_50']
    rsi = df['RSI_14']
    
    curr = -2 # Check the last closed 1h candle? Or the currently evolving 1h candle?
    # The prompt says: "the 1h chart _also_ presently meets the entry criteria". Usually this implies the currently evolving candle, or the last closed. Let's use the current evolving one (-1) or last closed (-2). For momentum, last closed (-2) is safer to avoid repainting.
    
    if signal == "LONG":
        return ema20.iloc[-2] > ema50.iloc[-2] and 55 <= rsi.iloc[-2] <= 65
    elif signal == "SHORT":
        return ema20.iloc[-2] < ema50.iloc[-2] and 35 <= rsi.iloc[-2] <= 45
    return False

async def get_btc_pct_change(exchange):
    df = await fetch_ohlcv(exchange, "BTC/USDT", "15m", limit=100)
    if df is None or len(df) < 20: return 0.0
    close = df['close']
    curr = -2
    base_idx = curr - 16
    return (close.iloc[curr] - close.iloc[base_idx]) / close.iloc[base_idx]

async def scan_market():
    logger.info("Starting market scan...")
    pairs_file = os.path.join(os.path.dirname(__file__), "pairs.txt")
    if not os.path.exists(pairs_file):
        logger.error("pairs.txt not found.")
        return []
        
    with open(pairs_file, "r") as f:
        symbols = [line.strip() for line in f if line.strip()]
        
    exchange = ccxt.binance({'enableRateLimit': True})
    
    try:
        btc_pct = await get_btc_pct_change(exchange)
        logger.info(f"Analyzing {len(symbols)} pairs for 15m exact crosses...")
        tasks = [check_pair_15m(exchange, symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks)
        
        signals = []
        for i, res in enumerate(results):
            if res:
                # Calculate priority score
                score = res['coin_pct'] - btc_pct
                res['score'] = score
                # The index `i` maps sequentially to `symbols` which is ordered by market cap in pairs.txt
                res['mc_rank'] = i 
                
                # Check 1h confluence
                confluence = await check_pair_1h(exchange, res['symbol'], res['signal'])
                if confluence:
                    signals.append(res)
                    
        # Sort by score desc, then market cap rank (lower index = better MC)
        signals.sort(key=lambda x: (-x['score'], x['mc_rank']))
        
        logger.info(f"Scan complete. Found {len(signals)} signals matching 15m + 1h confluence.")
        return signals[:10]
        
    finally:
        await exchange.close()

# test block
if __name__ == "__main__":
    asyncio.run(scan_market())
