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

async def check_4h_trend(exchange, symbol):
    """Check 4H EMA 200 trend filter. Returns 'LONG', 'SHORT', or None."""
    df = await fetch_ohlcv(exchange, symbol, "4h", limit=210)
    if df is None or len(df) < 201:
        return None
    
    df.ta.ema(length=200, append=True)
    
    curr = -2  # Last closed 4H candle
    price = df['close'].iloc[curr]
    ema200 = df['EMA_200'].iloc[curr]
    
    if pd.isna(ema200):
        return None
    
    if price > ema200:
        return "LONG"
    elif price < ema200:
        return "SHORT"
    return None

async def check_1h_entry(exchange, symbol, trend_direction):
    """Check 1H entry conditions: EMA alignment + MACD cross + ATR.
    Returns signal dict or None."""
    df = await fetch_ohlcv(exchange, symbol, "1h", limit=100)
    if df is None or len(df) < 51:
        return None
    
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.atr(length=14, append=True)
    
    ema20 = df['EMA_20']
    ema50 = df['EMA_50']
    macd_line = df['MACD_12_26_9']
    macd_signal = df['MACDs_12_26_9']
    macd_hist = df['MACDh_12_26_9']
    atr = df['ATRr_14']
    
    curr = -2  # Last closed 1H candle
    prev = -3  # Previous closed 1H candle
    
    # Check for NaN values in critical indicators
    if any(pd.isna(x) for x in [ema20.iloc[curr], ema50.iloc[curr], 
                                   macd_line.iloc[curr], macd_signal.iloc[curr],
                                   macd_hist.iloc[curr], atr.iloc[curr],
                                   macd_line.iloc[prev], macd_signal.iloc[prev]]):
        return None
    
    signal = None
    
    if trend_direction == "LONG":
        # EMA Alignment: EMA 20 > EMA 50
        ema_aligned = ema20.iloc[curr] > ema50.iloc[curr]
        # MACD Trigger: MACD crosses above Signal AND Histogram is positive
        macd_cross = (macd_line.iloc[prev] <= macd_signal.iloc[prev]) and \
                     (macd_line.iloc[curr] > macd_signal.iloc[curr])
        hist_confirm = macd_hist.iloc[curr] > 0
        
        if ema_aligned and macd_cross and hist_confirm:
            signal = "LONG"
            
    elif trend_direction == "SHORT":
        # EMA Alignment: EMA 20 < EMA 50
        ema_aligned = ema20.iloc[curr] < ema50.iloc[curr]
        # MACD Trigger: MACD crosses below Signal AND Histogram is negative
        macd_cross = (macd_line.iloc[prev] >= macd_signal.iloc[prev]) and \
                     (macd_line.iloc[curr] < macd_signal.iloc[curr])
        hist_confirm = macd_hist.iloc[curr] < 0
        
        if ema_aligned and macd_cross and hist_confirm:
            signal = "SHORT"
    
    if not signal:
        return None
    
    price = df['close'].iloc[curr]
    atr_val = atr.iloc[curr]
    
    return {
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "atr": atr_val
    }

async def get_btc_pct_change(exchange):
    """Calculate BTC % change over the last 4 hours using 1H candles."""
    df = await fetch_ohlcv(exchange, "BTC/USDT", "1h", limit=10)
    if df is None or len(df) < 6:
        return 0.0
    close = df['close']
    curr = -2
    base_idx = curr - 4  # 4 hours back
    return (close.iloc[curr] - close.iloc[base_idx]) / close.iloc[base_idx]

async def scan_market():
    logger.info("Starting 1H/4H swing scan...")
    pairs_file = os.path.join(os.path.dirname(__file__), "pairs.txt")
    if not os.path.exists(pairs_file):
        logger.error("pairs.txt not found.")
        return []
        
    with open(pairs_file, "r") as f:
        symbols = [line.strip() for line in f if line.strip()]
        
    exchange = ccxt.binance({'enableRateLimit': True})
    
    try:
        btc_pct = await get_btc_pct_change(exchange)
        logger.info(f"Analyzing {len(symbols)} pairs (4H trend + 1H MACD entry)...")
        
        # Step 1: Check 4H trend for all pairs in parallel
        trend_tasks = [check_4h_trend(exchange, symbol) for symbol in symbols]
        trend_results = await asyncio.gather(*trend_tasks)
        
        # Build list of pairs that pass the 4H filter
        filtered_pairs = []
        for symbol, trend in zip(symbols, trend_results):
            if trend is not None:
                filtered_pairs.append((symbol, trend))
        
        logger.info(f"{len(filtered_pairs)} pairs pass 4H EMA 200 trend filter.")
        
        # Step 2: Check 1H entry for filtered pairs in parallel
        entry_tasks = [check_1h_entry(exchange, sym, trend) for sym, trend in filtered_pairs]
        entry_results = await asyncio.gather(*entry_tasks)
        
        signals = []
        for i, res in enumerate(entry_results):
            if res:
                # Calculate relative strength vs BTC
                sym = filtered_pairs[i][0]
                # Use the pair's index in the original symbols list for MC rank
                mc_rank = symbols.index(sym) if sym in symbols else i
                
                # Fetch coin's 4h pct change for relative strength
                coin_df = await fetch_ohlcv(exchange, sym, "1h", limit=10)
                if coin_df is not None and len(coin_df) >= 6:
                    close = coin_df['close']
                    coin_pct = (close.iloc[-2] - close.iloc[-6]) / close.iloc[-6]
                else:
                    coin_pct = 0.0
                
                score = coin_pct - btc_pct
                res['score'] = score
                res['mc_rank'] = mc_rank
                signals.append(res)
                    
        # Sort by score desc, then market cap rank
        signals.sort(key=lambda x: (-x['score'], x['mc_rank']))
        
        logger.info(f"Scan complete. Found {len(signals)} signals matching 4H trend + 1H MACD entry.")
        return signals[:10]
        
    finally:
        await exchange.close()

# test block
if __name__ == "__main__":
    asyncio.run(scan_market())
