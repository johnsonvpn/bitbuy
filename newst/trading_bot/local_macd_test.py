#!/usr/bin/env python3
"""
本地获取 MACD 数据，尝试多种判断逻辑
"""
import pandas as pd
import ccxt
from datetime import datetime, timedelta

# 初始化交易所
exchange = ccxt.binance({
    'options': {'defaultType': 'future'}
})

symbol = "BTC/USDT:USDT"

# 获取K线数据
def fetch_klines(timeframe, limit=100):
    klines = exchange.fetch_ohlcv(symbol, timeframe, None, limit)
    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')
    return df

# 尝试不同的 MACD 参数
def try_macd_params(df, fast, slow, signal, name):
    s = pd.Series(df['close'].values)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist

print("=" * 100)
print("获取最近 48 根 K 线数据，尝试多种 MACD 判断逻辑")
print("=" * 100)

# 获取 1H 数据
print("\n" + "=" * 100)
print("1H 数据")
print("=" * 100)
df1h = fetch_klines('1h', 48)
print(f"数据范围: {df1h['timestamp'].iloc[0]} ~ {df1h['timestamp'].iloc[-1]}")
print(f"K线数量: {len(df1h)}")

# 尝试标准参数 (12, 26, 9)
df1h['dif_std'], df1h['dea_std'], df1h['hist_std'] = try_macd_params(df1h, 12, 26, 9, "标准")
# 尝试参数 (9, 21, 60)
df1h['dif_9'], df1h['dea_9'], df1h['hist_9'] = try_macd_params(df1h, 9, 21, 60, "参数2")

# 打印最近24根K线，增加动能变化判断
print("\n最近 24 根 1H K线:")
print("-" * 130)
print(f"{'时间':<20} {'收盘价':>10} {'DIF':>12} {'DEA':>12} {'HIST':>12} {'DIF趋势':<10} {'HIST趋势':<10} {'动能变化':<12} {'综合判断':<10}")
print("-" * 130)
for i in range(-24, 0):
    row = df1h.iloc[i]
    ts = row['timestamp'].strftime("%Y-%m-%d %H:%M")
    close = row['close']
    dif = row['dif_std']
    dea = row['dea_std']
    hist = row['hist_std']

    # DIF 趋势
    dif_trend = "long" if dif > 0 else "short"

    # HIST 趋势
    hist_trend = "long" if hist > 0 else "short"

    # 动能变化（看 HIST 的变化方向）
    hist_change = 0
    if i > -24:
        prev_hist = df1h.iloc[i-1]['hist_std']
        hist_change = hist - prev_hist
        if hist > 0:
            momentum = "增强↑" if hist_change > 0 else "减弱↓"
        else:
            momentum = "减弱↑" if hist_change > 0 else "增强↓"
    else:
        momentum = "N/A"

    # 综合判断：结合 DIF 位置和动能变化
    if dif > 0 and hist > 0 and hist_change > 0:
        final = "强势多"
    elif dif > 0 and hist > 0 and hist_change < 0:
        final = "弱势多"
    elif dif < 0 and hist < 0 and hist_change < 0:
        final = "强势空"
    elif dif < 0 and hist < 0 and hist_change > 0:
        final = "弱势空"
    elif hist > 0 and dif < 0:
        final = "反弹中"
    elif hist < 0 and dif > 0:
        final = "回调中"
    else:
        final = "震荡"

    print(f"{ts:<20} {close:>10.2f} {dif:>12.4f} {dea:>12.4f} {hist:>12.4f} {dif_trend:<10} {hist_trend:<10} {momentum:<12} {final:<10}")

# 获取 4H 数据
print("\n" + "=" * 100)
print("4H 数据")
print("=" * 100)
df4h = fetch_klines('4h', 48)
print(f"数据范围: {df4h['timestamp'].iloc[0]} ~ {df4h['timestamp'].iloc[-1]}")
print(f"K线数量: {len(df4h)}")

# 尝试标准参数 (12, 26, 9)
df4h['dif_std'], df4h['dea_std'], df4h['hist_std'] = try_macd_params(df4h, 12, 26, 9, "标准")
# 尝试参数 (9, 21, 60)
df4h['dif_9'], df4h['dea_9'], df4h['hist_9'] = try_macd_params(df4h, 9, 21, 60, "参数2")

# 打印最近24根K线，增加动能变化判断
print("\n最近 24 根 4H K线:")
print("-" * 130)
print(f"{'时间':<20} {'收盘价':>10} {'DIF':>12} {'DEA':>12} {'HIST':>12} {'DIF趋势':<10} {'HIST趋势':<10} {'动能变化':<12} {'综合判断':<10}")
print("-" * 130)
for i in range(-24, 0):
    row = df4h.iloc[i]
    ts = row['timestamp'].strftime("%Y-%m-%d %H:%M")
    close = row['close']
    dif = row['dif_std']
    dea = row['dea_std']
    hist = row['hist_std']

    # DIF 趋势
    dif_trend = "long" if dif > 0 else "short"

    # HIST 趋势
    hist_trend = "long" if hist > 0 else "short"

    # 动能变化（看 HIST 的变化方向）
    hist_change = 0
    if i > -24:
        prev_hist = df4h.iloc[i-1]['hist_std']
        hist_change = hist - prev_hist
        if hist > 0:
            momentum = "增强↑" if hist_change > 0 else "减弱↓"
        else:
            momentum = "减弱↑" if hist_change > 0 else "增强↓"
    else:
        momentum = "N/A"

    # 综合判断：结合 DIF 位置和动能变化
    if dif > 0 and hist > 0 and hist_change > 0:
        final = "强势多"
    elif dif > 0 and hist > 0 and hist_change < 0:
        final = "弱势多"
    elif dif < 0 and hist < 0 and hist_change < 0:
        final = "强势空"
    elif dif < 0 and hist < 0 and hist_change > 0:
        final = "弱势空"
    elif hist > 0 and dif < 0:
        final = "反弹中"
    elif hist < 0 and dif > 0:
        final = "回调中"
    else:
        final = "震荡"

    print(f"{ts:<20} {close:>10.2f} {dif:>12.4f} {dea:>12.4f} {hist:>12.4f} {dif_trend:<10} {hist_trend:<10} {momentum:<12} {final:<10}")

print("\n" + "=" * 100)
print("判断逻辑说明:")
print("=" * 100)
print("1. DIF > 0 → long, DIF < 0 → short  (看DIF是否在零轴上方)")
print("2. HIST > 0 → long, HIST < 0 → short (看MACD柱是否在零轴上方)")
print("3. DIF > DEA → 金叉(看多), DIF < DEA → 死叉(看空)")
print("\n请对照图形，选择最接近的判断逻辑！")
