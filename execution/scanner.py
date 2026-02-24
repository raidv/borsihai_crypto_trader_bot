"""Market scanner — 4H/1H swing strategy with composite signal scoring."""
import asyncio
import ccxt.async_support as ccxt
import pandas as pd
import pandas_ta as ta
import numpy as np
import logging
import os

logger = logging.getLogger(__name__)


async def fetch_ohlcv(exchange, symbol, timeframe, limit=100):
    """Fetch OHLCV data and return as DataFrame."""
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
    Returns signal dict (with indicator data for scoring) or None."""
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
        ema_aligned = ema20.iloc[curr] > ema50.iloc[curr]
        macd_cross = (macd_line.iloc[prev] <= macd_signal.iloc[prev]) and \
                     (macd_line.iloc[curr] > macd_signal.iloc[curr])
        hist_confirm = macd_hist.iloc[curr] > 0

        if ema_aligned and macd_cross and hist_confirm:
            signal = "LONG"

    elif trend_direction == "SHORT":
        ema_aligned = ema20.iloc[curr] < ema50.iloc[curr]
        macd_cross = (macd_line.iloc[prev] >= macd_signal.iloc[prev]) and \
                     (macd_line.iloc[curr] < macd_signal.iloc[curr])
        hist_confirm = macd_hist.iloc[curr] < 0

        if ema_aligned and macd_cross and hist_confirm:
            signal = "SHORT"

    if not signal:
        return None

    price = df['close'].iloc[curr]
    atr_val = atr.iloc[curr]

    # Collect indicator data for composite scoring
    indicator_data = {
        "macd_hist_current": macd_hist.iloc[curr],
        "macd_hist_previous": macd_hist.iloc[prev],
        "macd_hist_series": macd_hist.dropna().tail(20).tolist(),
        "ema20": ema20.iloc[curr],
        "ema50": ema50.iloc[curr],
        "volume_current": df['volume'].iloc[curr],
        "volume_series": df['volume'].dropna().tail(20).tolist(),
        "candle_body": abs(df['close'].iloc[curr] - df['open'].iloc[curr]),
        "atr_val": atr_val,
    }

    return {
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "atr": atr_val,
        "indicator_data": indicator_data,
    }


def compute_signal_score(indicator_data, btc_relative_strength):
    """Compute a composite signal strength score from 0-100.

    Components (weighted):
    - MACD histogram magnitude (20%): current histogram vs recent range
    - MACD histogram acceleration (15%): growth rate of histogram
    - EMA 20/50 spread (15%): distance between EMAs normalized by price
    - Volume spike (20%): current volume vs 20-bar average
    - ATR-relative price move (15%): candle body vs ATR (sharpness)
    - Relative strength vs BTC (15%): coin performance vs BTC

    Returns dict with composite score and component breakdown.
    """
    scores = {}

    # 1. MACD Histogram Magnitude (20%)
    hist_series = indicator_data.get("macd_hist_series", [])
    hist_current = abs(indicator_data.get("macd_hist_current", 0))
    if hist_series and len(hist_series) >= 2:
        hist_abs = [abs(h) for h in hist_series]
        hist_max = max(hist_abs) if max(hist_abs) > 0 else 1
        scores["macd_magnitude"] = min(100, (hist_current / hist_max) * 100)
    else:
        scores["macd_magnitude"] = 50

    # 2. MACD Histogram Acceleration (15%)
    hist_prev = abs(indicator_data.get("macd_hist_previous", 0))
    if hist_prev > 0:
        acceleration = (hist_current - hist_prev) / hist_prev
        # Clamp to [-1, 3] range, map to [0, 100]
        acceleration = max(-1, min(3, acceleration))
        scores["macd_acceleration"] = ((acceleration + 1) / 4) * 100
    elif hist_current > 0:
        scores["macd_acceleration"] = 100  # From zero to positive = strong start
    else:
        scores["macd_acceleration"] = 50

    # 3. EMA 20/50 Spread (15%)
    ema20 = indicator_data.get("ema20", 0)
    ema50 = indicator_data.get("ema50", 0)
    if ema50 > 0:
        ema_spread_pct = abs(ema20 - ema50) / ema50 * 100
        # Typical spread: 0-5%. Map 0 → 0, 3%+ → 100
        scores["ema_spread"] = min(100, (ema_spread_pct / 3.0) * 100)
    else:
        scores["ema_spread"] = 50

    # 4. Volume Spike (20%)
    vol_series = indicator_data.get("volume_series", [])
    vol_current = indicator_data.get("volume_current", 0)
    if vol_series and len(vol_series) >= 2:
        vol_avg = sum(vol_series) / len(vol_series)
        if vol_avg > 0:
            vol_ratio = vol_current / vol_avg
            # 1.0 = average (score 50), 2.0+ = high (score 100)
            scores["volume"] = min(100, max(0, (vol_ratio - 0.5) / 1.5 * 100))
        else:
            scores["volume"] = 50
    else:
        scores["volume"] = 50

    # 5. ATR-relative Price Move (15%)
    candle_body = indicator_data.get("candle_body", 0)
    atr_val = indicator_data.get("atr_val", 0)
    if atr_val > 0:
        body_atr_ratio = candle_body / atr_val
        # 0.5 ATR body = moderate (50), 1.5+ ATR body = very sharp (100)
        scores["atr_move"] = min(100, max(0, (body_atr_ratio / 1.5) * 100))
    else:
        scores["atr_move"] = 50

    # 6. Relative Strength vs BTC (15%)
    # btc_relative_strength is typically -0.10 to +0.10
    rs = btc_relative_strength
    # Map: -5% → 0, 0% → 50, +5% → 100
    scores["btc_relative"] = min(100, max(0, (rs + 0.05) / 0.10 * 100))

    # Weighted composite
    weights = {
        "macd_magnitude": 0.20,
        "macd_acceleration": 0.15,
        "ema_spread": 0.15,
        "volume": 0.20,
        "atr_move": 0.15,
        "btc_relative": 0.15,
    }

    composite = sum(scores[k] * weights[k] for k in weights)
    composite = round(min(100, max(0, composite)))

    return {
        "composite": composite,
        "components": scores,
    }


def format_score_label(composite):
    """Return a human-readable label for the composite score."""
    if composite >= 80:
        return "🔥 Very Strong"
    elif composite >= 60:
        return "💪 Strong"
    elif composite >= 40:
        return "📊 Moderate"
    elif composite >= 20:
        return "⚠️ Weak"
    else:
        return "❄️ Very Weak"


def _make_bar(value, width=10):
    """Create a visual bar chart: ███████░░░"""
    filled = round(value / 100 * width)
    return "█" * filled + "░" * (width - filled)


def format_score_display(score_data, btc_relative):
    """Format the score display for Telegram alert messages."""
    composite = score_data["composite"]
    c = score_data["components"]
    label = format_score_label(composite)

    btc_pct = btc_relative * 100
    btc_sign = "+" if btc_pct >= 0 else ""

    return (
        f"Score: {composite}/100 ({label})\n"
        f"  MACD: {_make_bar(c['macd_magnitude'])} | "
        f"Vol: {_make_bar(c['volume'])} | "
        f"vs BTC: {btc_sign}{btc_pct:.1f}%"
    )


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
    """Run full market scan.

    Returns a dict with:
    - signals: list of signal dicts (max 10)
    - metadata: scan statistics for summary messages
    """
    logger.info("Starting 1H/4H swing scan...")
    pairs_file = os.path.join(os.path.dirname(__file__), "pairs.txt")
    if not os.path.exists(pairs_file):
        logger.error("pairs.txt not found.")
        return {"signals": [], "metadata": {"pairs_scanned": 0, "filtered_count": 0, "signals_found": 0}}

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
                sym = filtered_pairs[i][0]
                mc_rank = symbols.index(sym) if sym in symbols else i

                # Fetch coin's 4h pct change for relative strength
                coin_df = await fetch_ohlcv(exchange, sym, "1h", limit=10)
                if coin_df is not None and len(coin_df) >= 6:
                    close = coin_df['close']
                    coin_pct = (close.iloc[-2] - close.iloc[-6]) / close.iloc[-6]
                else:
                    coin_pct = 0.0

                btc_relative = coin_pct - btc_pct
                res['btc_relative'] = btc_relative
                res['mc_rank'] = mc_rank

                # Compute composite score
                indicator_data = res.pop("indicator_data", {})
                score_data = compute_signal_score(indicator_data, btc_relative)
                res['score'] = score_data["composite"]
                res['score_data'] = score_data
                res['score_display'] = format_score_display(score_data, btc_relative)

                signals.append(res)

        # Sort by composite score desc, then market cap rank
        signals.sort(key=lambda x: (-x['score'], x['mc_rank']))

        logger.info(f"Scan complete. Found {len(signals)} signals matching 4H trend + 1H MACD entry.")

        metadata = {
            "pairs_scanned": len(symbols),
            "filtered_count": len(filtered_pairs),
            "signals_found": len(signals),
        }

        return {"signals": signals[:10], "metadata": metadata}

    finally:
        await exchange.close()


# test block
if __name__ == "__main__":
    asyncio.run(scan_market())
