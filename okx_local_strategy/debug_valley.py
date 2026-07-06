#!/usr/bin/env python3
"""云端调试波谷检测"""
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

# 检测波谷
print('\n波谷检测：')
valleys = []
for i in range(1, len(df)-1):
    prev_close = df['close'].iloc[i-1]
    curr_close = df['close'].iloc[i]
    next_close = df['close'].iloc[i+1]
    if curr_close < prev_close and curr_close < next_close:
        ts = df['timestamp'].iloc[i] + pd.Timedelta(hours=8)
        valleys.append((ts, curr_close))
        print(f"  波谷: {ts.strftime('%m-%d %H:%M')} | ${curr_close:.2f}")

if valleys:
    print(f'\n最后一个波谷: {valleys[-1][0].strftime("%m-%d %H:%M")} | ${valleys[-1][1]:.2f}')
