#!/usr/bin/env python3
"""云端调试波谷检测 - V/U型算法"""
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

print('最近20根K线（北京时间）：')
print('='*70)
for idx, row in df.tail(20).iterrows():
    ts = row['timestamp'] + pd.Timedelta(hours=8)
    print(f"{ts.strftime('%m-%d %H:%M')} | 收盘: ${row['close']:.2f}")

# 新的V/U型波谷检测算法
print('\nV/U型波谷检测：')
valleys = []
for i in range(2, len(df)-2):
    window_closes = [
        df['close'].iloc[i-2],
        df['close'].iloc[i-1],
        df['close'].iloc[i],
        df['close'].iloc[i+1],
        df['close'].iloc[i+2]
    ]
    
    # 波谷：当前是窗口内最低点，且前后形成上升趋势
    if df['close'].iloc[i] == min(window_closes):
        if window_closes[0] > window_closes[1] and window_closes[3] < window_closes[4]:
            ts = df['timestamp'].iloc[i] + pd.Timedelta(hours=8)
            valleys.append((ts, df['close'].iloc[i]))
            print(f"  波谷: {ts.strftime('%m-%d %H:%M')} | ${df['close'].iloc[i]:.2f}")

print('\nV/U型波峰检测：')
peaks = []
for i in range(2, len(df)-2):
    window_closes = [
        df['close'].iloc[i-2],
        df['close'].iloc[i-1],
        df['close'].iloc[i],
        df['close'].iloc[i+1],
        df['close'].iloc[i+2]
    ]
    
    # 波峰：当前是窗口内最高点，且前后形成下降趋势
    if df['close'].iloc[i] == max(window_closes):
        if window_closes[0] < window_closes[1] and window_closes[3] > window_closes[4]:
            ts = df['timestamp'].iloc[i] + pd.Timedelta(hours=8)
            peaks.append((ts, df['close'].iloc[i]))
            print(f"  波峰: {ts.strftime('%m-%d %H:%M')} | ${df['close'].iloc[i]:.2f}")

if valleys:
    print(f'\n最后一个波谷: {valleys[-1][0].strftime("%m-%d %H:%M")} | ${valleys[-1][1]:.2f}')
if peaks:
    print(f'最后一个波峰: {peaks[-1][0].strftime("%m-%d %H:%M")} | ${peaks[-1][1]:.2f}')
