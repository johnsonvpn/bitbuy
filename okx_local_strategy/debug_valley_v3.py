#!/usr/bin/env python3
"""云端调试 - 检查1779.66附近是否是有效的V/U型波谷"""
import ccxt
import pandas as pd

exchange = ccxt.okx({
    'apiKey': '',
    'secret': '',
    'options': {'defaultType': 'swap'},
})

# 获取最近50根1h K线
ohlcv = exchange.fetch_ohlcv('ETH/USDT', '1h', limit=50)
df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

print('最近25根K线（北京时间）：')
print('='*70)
for idx, row in df.tail(25).iterrows():
    ts = row['timestamp'] + pd.Timedelta(hours=8)
    print(f"{ts.strftime('%m-%d %H:%M')} | 收盘: ${row['close']:.2f}")

# 严格的V/U型波谷检测算法
def detect_peaks_valleys(df1h):
    if len(df1h) < 5:
        return None, None
    
    peaks = []
    valleys = []
    
    for i in range(2, len(df1h)-2):
        window_closes = [
            df1h['close'].iloc[i-2],
            df1h['close'].iloc[i-1],
            df1h['close'].iloc[i],
            df1h['close'].iloc[i+1],
            df1h['close'].iloc[i+2]
        ]
        
        # 波谷：当前是窗口内最低点，且前后都是上升趋势
        if df1h['close'].iloc[i] == min(window_closes):
            if window_closes[0] > window_closes[1] and window_closes[3] < window_closes[4]:
                valleys.append((i, df1h['close'].iloc[i]))
                ts = df1h['timestamp'].iloc[i] + pd.Timedelta(hours=8)
                print(f"\n✅ 检测到V型波谷: {ts.strftime('%m-%d %H:%M')} | ${df1h['close'].iloc[i]:.2f}")
                print(f"   窗口: {window_closes[0]:.2f} -> {window_closes[1]:.2f} -> {window_closes[2]:.2f} -> {window_closes[3]:.2f} -> {window_closes[4]:.2f}")
                print(f"   条件1: {window_closes[0]:.2f} > {window_closes[1]:.2f} = {window_closes[0] > window_closes[1]}")
                print(f"   条件2: {window_closes[3]:.2f} < {window_closes[4]:.2f} = {window_closes[3] < window_closes[4]}")
        
        # 波峰
        elif df1h['close'].iloc[i] == max(window_closes):
            if window_closes[0] < window_closes[1] and window_closes[3] > window_closes[4]:
                peaks.append((i, df1h['close'].iloc[i]))
    
    return peaks, valleys

print('\n' + '='*70)
print('V/U型波谷检测结果：')
print('='*70)
valleys = detect_peaks_valleys(df)

print('\n所有检测到的波谷：')
for idx, price in valleys[0] if valleys[0] else []:
    ts = df['timestamp'].iloc[idx] + pd.Timedelta(hours=8)
    print(f"  {ts.strftime('%m-%d %H:%M')} | ${price:.2f}")

if valleys[0]:
    last_valley = valleys[0][-1]
    ts = df['timestamp'].iloc[last_valley[0]] + pd.Timedelta(hours=8)
    print(f'\n🎯 最后一个波谷: {ts.strftime("%m-%d %H:%M")} | ${last_valley[1]:.2f}')
    print(f'   当前价格: ${df["close"].iloc[-1]:.2f}')
    print(f'   当前价格 < 波谷? {df["close"].iloc[-1] < last_valley[1]}')
