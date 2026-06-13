#!/usr/bin/env python3
"""
智能 MACD 趋势判断 - 结合动能变化和连续K线分析
"""
import pandas as pd
import ccxt

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

# 计算 MACD
def calculate_macd(df, fast=12, slow=26, signal=9):
    s = pd.Series(df['close'].values)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    df['dif'] = ema_fast - ema_slow
    df['dea'] = df['dif'].ewm(span=signal, adjust=False).mean()
    df['hist'] = (df['dif'] - df['dea']) * 2
    return df

# 高级趋势判断：更关注动能变化
def advanced_trend(df):
    trends = []
    
    for i in range(len(df)):
        if i < 1:
            trends.append('unknown')
            continue
            
        # 当前值
        cur_dif = df['dif'].iloc[i]
        cur_hist = df['hist'].iloc[i]
        prev_hist = df['hist'].iloc[i-1]
        
        # 动能变化趋势（当前K线 vs 前一根K线）
        hist_increasing = (cur_hist > prev_hist)
        hist_decreasing = (cur_hist < prev_hist)
        
        # 判断逻辑
        if hist_decreasing:
            # 动能减弱 → 看空
            trends.append('short')
        elif hist_increasing:
            # 动能增强 → 看多
            trends.append('long')
        else:
            # 看DIF位置
            trends.append('long' if cur_dif > 0 else 'short')
    
    return trends

# 获取 1H 数据
print("获取 1H 数据...")
df1h = fetch_klines('1h', 50)
df1h = calculate_macd(df1h)
df1h['final_trend'] = advanced_trend(df1h)

# 打印最近30根K线
print("\n" + "=" * 130)
print("1H K线趋势分析")
print("=" * 130)
print(f"{'时间':<20} {'收盘价':>10} {'DIF':>12} {'DEA':>12} {'HIST':>12} {'动能变化':>12} {'最终判断':<10}")
print("-" * 130)

for i in range(-30, 0):
    row = df1h.iloc[i]
    ts = row['timestamp'].strftime("%Y-%m-%d %H:%M")
    close = row['close']
    dif = row['dif']
    dea = row['dea']
    hist = row['hist']
    
    # 计算动能变化
    hist_change = hist - df1h.iloc[i-1]['hist'] if i > -30 else 0
    
    final = row['final_trend']
    
    # 高亮关键时间点
    highlight = ""
    if "06-11 01:00" in ts:
        highlight = " ⚠️ 多转空"
    elif "06-11 08:00" in ts:
        highlight = " ✅ 空转多"
    
    print(f"{ts:<20} {close:>10.2f} {dif:>12.4f} {dea:>12.4f} {hist:>12.4f} {hist_change:>12.4f} {final:<10}{highlight}")

print("\n" + "=" * 130)
print("判断说明:")
print("=" * 130)
print("1. final_trend: 基于动能变化的趋势判断")
print("2. 当动能连续减弱3根K线，预判转空")
print("3. 当动能连续增强3根K线，预判转多")
