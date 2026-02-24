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


def calc_pct(val, arr):
    """Calculate percentile of a value in an array (0 to 100)."""
    if not arr: return 50.0
    return sum(1 for x in arr if x < val) / len(arr) * 100.0


async def check_1h_entry(exchange, symbol, regime_4h):
    """Check 1H entry conditions: Dual Path (TA and CT).
    Returns signal dict or None."""
    df = await fetch_ohlcv(exchange, symbol, "1h", limit=150)
    if df is None or len(df) < 100:
        return None

    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.atr(length=14, append=True)

    ema20 = df['EMA_20']
    ema50 = df['EMA_50']
    macd_hist = df['MACDh_12_26_9']
    atr = df['ATRr_14']
    close = df['close']
    volume = df['volume']

    curr = -2
    price = close.iloc[curr]

    if any(pd.isna(x) for x in [ema20.iloc[curr], ema50.iloc[curr], macd_hist.iloc[curr], atr.iloc[curr]]):
        return None

    hist_curr = macd_hist.iloc[curr]
    if hist_curr > 0:
        trade_dir = "LONG"
        dir_mult = 1
    elif hist_curr < 0:
        trade_dir = "SHORT"
        dir_mult = -1
    else:
        return None

    path = "TA" if trade_dir == regime_4h else "CT"

    hist_series = macd_hist.iloc[-52:-1].dropna().tolist()
    if len(hist_series) < 50:
        return None

    hist_deltas = [dir_mult * (hist_series[i] - hist_series[i-1]) for i in range(1, len(hist_series))]
    delta_curr = hist_deltas[-1]

    hist_mags = [abs(x) for x in hist_series[1:]]
    mag_curr = abs(hist_curr)

    delta_pct = calc_pct(delta_curr, hist_deltas)
    mag_pct = calc_pct(mag_curr, hist_mags)

    vol_20 = volume.iloc[-22:-1].tolist()
    vol_curr = vol_20[-1]
    vol_pct = calc_pct(vol_curr, vol_20)

    persistence = 0
    for h in reversed(hist_series):
        if (h > 0 and trade_dir == "LONG") or (h < 0 and trade_dir == "SHORT"):
            persistence += 1
        else:
            break

    signal = None
    is_breakout = False

    if trade_dir == "LONG":
        highest_12 = close.iloc[-14:-2].max()
        is_breakout = price >= highest_12
    else:
        lowest_12 = close.iloc[-14:-2].min()
        is_breakout = price <= lowest_12

    if path == "TA":
        if persistence >= 2:
            signal = trade_dir
    else:
        req_persist = persistence >= 3
        last_3_deltas = hist_deltas[-3:]
        req_explosive = any(d >= np.percentile(hist_deltas, 90) for d in last_3_deltas) and (np.mean(last_3_deltas) >= np.percentile(hist_deltas, 70))
        
        req_structure = price > ema50.iloc[curr] if trade_dir == "LONG" else price < ema50.iloc[curr]
        req_confirm = is_breakout
        req_volume = vol_pct >= 70.0

        if req_persist and req_explosive and req_structure and req_confirm and req_volume:
            signal = trade_dir

    if not signal:
        return None

    high_low_range = df['high'].iloc[curr] - df['low'].iloc[curr]
    body = abs(close.iloc[curr] - df['open'].iloc[curr])
    body_ratio = body / high_low_range if high_low_range > 0 else 0

    indicator_data = {
        "persistence": persistence,
        "delta_pct": delta_pct,
        "mag_pct": mag_pct,
        "ema20": ema20.iloc[curr],
        "ema50": ema50.iloc[curr],
        "price": price,
        "atr_val": atr.iloc[curr],
        "vol_pct": vol_pct,
        "body_ratio": body_ratio,
        "path": path,
        "regime_4h": regime_4h,
        "trade_dir": trade_dir,
        "is_breakout": is_breakout
    }

    return {
        "symbol": symbol,
        "signal": signal,
        "path": path,
        "price": price,
        "atr": atr.iloc[curr],
        "indicator_data": indicator_data,
    }


def compute_signal_score(indicator_data, btc_relative_strength):
    """Compute a composite signal strength score from 0-100 using unifying Pillars."""
    scores = {}
    path = indicator_data["path"]
    trade_dir = indicator_data["trade_dir"]

    # 1. Momentum (40 pts)
    p = indicator_data["persistence"]
    scores["persistence"] = min(12.0, max(0.0, (p - 1) * 6.0))
    scores["delta_pct"] = (indicator_data["delta_pct"] / 100.0) * 18.0
    scores["mag_pct"] = (indicator_data["mag_pct"] / 100.0) * 10.0

    # 2. Structure (25 pts)
    price = indicator_data["price"]
    ema20 = indicator_data["ema20"]
    ema50 = indicator_data["ema50"]
    if trade_dir == "LONG" and ema20 > ema50:
        scores["ema_alignment"] = 12.0
    elif trade_dir == "SHORT" and ema20 < ema50:
        scores["ema_alignment"] = 12.0
    else:
        scores["ema_alignment"] = 0.0

    scores["breakout"] = 8.0 if indicator_data["is_breakout"] else 0.0

    atr = indicator_data["atr_val"]
    dist = abs(price - ema20)
    if atr > 0:
        chase_ratio = dist / atr
        if chase_ratio <= 1.0:
            scores["anti_chase"] = 5.0
        elif chase_ratio <= 1.5:
            scores["anti_chase"] = 5.0 - ((chase_ratio - 1.0) * 10.0)
        else:
            scores["anti_chase"] = 0.0
    else:
        scores["anti_chase"] = 0.0

    # 3. Cleanliness (20 pts)
    scores["volume"] = (indicator_data["vol_pct"] / 100.0) * 12.0
    scores["wick_safety"] = indicator_data["body_ratio"] * 8.0

    # 4. Context (15 pts)
    regime_4h = indicator_data["regime_4h"]
    scores["context_4h"] = 10.0 if trade_dir == regime_4h else 0.0

    rs = btc_relative_strength
    if trade_dir == "SHORT":
        rs = -rs
    rs_clamped = min(0.05, max(-0.05, rs))
    scores["btc_rs"] = ((rs_clamped + 0.05) / 0.10) * 5.0

    total_score = sum(scores.values())
    
    return {
        "composite": round(min(100, max(0, total_score))),
        "components": scores
    }


def format_score_label(composite):
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


def _make_bar(value, max_val, width=10):
    filled = round((value / max_val) * width) if max_val > 0 else 0
    filled = min(width, max(0, filled))
    return "█" * filled + "░" * (width - filled)


def format_score_display(score_data, btc_relative, path):
    composite = score_data["composite"]
    c = score_data["components"]
    label = format_score_label(composite)

    btc_pct = btc_relative * 100
    btc_sign = "+" if btc_pct >= 0 else ""
    
    momentum_pts = c['persistence'] + c['delta_pct'] + c['mag_pct']

    return (
        f"Score: {composite}/100 ({label})\n"
        f"  Momntm: {_make_bar(momentum_pts, 40)} | "
        f"Struc: {_make_bar(c['ema_alignment'] + c['breakout'] + c['anti_chase'], 25)} \n"
        f"  Clean:  {_make_bar(c['volume'] + c['wick_safety'], 20)} | "
        f"vs BTC: {btc_sign}{btc_pct:.1f}%"
    )


async def get_btc_pct_change(exchange):
    df = await fetch_ohlcv(exchange, "BTC/USDT", "1h", limit=10)
    if df is None or len(df) < 6:
        return 0.0
    close = df['close']
    curr = -2
    base_idx = curr - 4
    return (close.iloc[curr] - close.iloc[base_idx]) / close.iloc[base_idx]


async def scan_market():
    logger.info("Starting 1H/4H swing scan (Hybrid)...")
    pairs_file = os.path.join(os.path.dirname(__file__), "pairs.txt")
    if not os.path.exists(pairs_file):
        logger.error("pairs.txt not found.")
        return {"signals": [], "metadata": {"pairs_scanned": 0, "filtered_count": 0, "signals_found": 0}}

    with open(pairs_file, "r") as f:
        symbols = [line.strip() for line in f if line.strip()]

    exchange = ccxt.binance({'enableRateLimit': True})

    try:
        btc_pct = await get_btc_pct_change(exchange)
        logger.info(f"Analyzing {len(symbols)} pairs (4H Regime + 1H Entry)...")

        trend_tasks = [check_4h_trend(exchange, symbol) for symbol in symbols]
        trend_results = await asyncio.gather(*trend_tasks)

        filtered_pairs = []
        for symbol, trend in zip(symbols, trend_results):
            if trend is not None:
                filtered_pairs.append((symbol, trend))

        logger.info(f"{len(filtered_pairs)} pairs passed 4H EMA 200 regime filter.")

        entry_tasks = [check_1h_entry(exchange, sym, trend) for sym, trend in filtered_pairs]
        entry_results = await asyncio.gather(*entry_tasks)

        signals = []
        for i, res in enumerate(entry_results):
            if res:
                sym = filtered_pairs[i][0]
                mc_rank = symbols.index(sym) if sym in symbols else i

                coin_df = await fetch_ohlcv(exchange, sym, "1h", limit=10)
                if coin_df is not None and len(coin_df) >= 6:
                    close = coin_df['close']
                    coin_pct = (close.iloc[-2] - close.iloc[-6]) / close.iloc[-6]
                else:
                    coin_pct = 0.0

                btc_relative = coin_pct - btc_pct
                res['btc_relative'] = btc_relative
                res['mc_rank'] = mc_rank

                indicator_data = res.pop("indicator_data", {})
                score_data = compute_signal_score(indicator_data, btc_relative)
                
                res['score'] = score_data["composite"]
                res['score_data'] = score_data
                res['score_display'] = format_score_display(score_data, btc_relative, res['path'])

                # Log the strict requirement formatting
                logger.info(
                    f"[{sym}] Path: {res['path']} | "
                    f"Hist_Delta_Pct: {indicator_data['delta_pct']:.1f} | "
                    f"Volume_Pct: {indicator_data['vol_pct']:.1f} | "
                    f"Total_Score: {res['score']}"
                )

                signals.append(res)

        signals.sort(key=lambda x: (-x['score'], x['mc_rank']))

        logger.info(f"Scan complete. Found {len(signals)} matching signals.")

        metadata = {
            "pairs_scanned": len(symbols),
            "filtered_count": len(filtered_pairs),
            "signals_found": len(signals),
        }

        return {"signals": signals[:10], "metadata": metadata}

    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(scan_market())
