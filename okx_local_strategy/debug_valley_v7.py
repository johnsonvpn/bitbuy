#!/usr/bin/env python3
"""查看06-16 17:00附近的数据"""
import ccxt
import pandas as pd

exchange = ccxt.okx({
    'apiKey': '',
    'secret': '',
    'options': {'defaultType': 'swap'},
})

ohlcv = exchange.fetch_ohlcv('ETH/USDT', '1h', limit=100)
df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

print('='*70)
print('06-16 10:00 至 06-17 06:00 K线数据：')
print('='*70)
for idx, row in df.iterrows():
    ts = row['timestamp'] + pd.Timedelta(hours=8)
    if '06-16 10' <= ts.strftime('%m-%d %H') <= '06-17 06':
        print(f"{ts.strftime('%m-%d %H:%M')} | O:${row['open']:.2f} H:${row['high']:.2f} L:${row['low']:.2f} C:${row['close']:.2f}")
