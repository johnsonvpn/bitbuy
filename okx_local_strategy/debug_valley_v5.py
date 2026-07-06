#!/usr/bin/env python3
"""云端调试 - 测试新的7根K线V/U型算法"""
import ccxt
import pandas as pd

exchange = ccxt.okx({
    'apiKey': '',
    'secret': '',
    'options': {'defaultType': 'swap'},
})

# 获取最近100根1h K线
ohlcv = exchange.fetch_ohlcv('ETH/USDT', '1h', limit=100)
df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

# 新的7根K线V/U型波谷检测算法
def detect_peaks_valleys(df1h):
    if len(df1h) < 7:
        return None, None

    peaks = []
    valleys = []

    for i in range(3, len(df1h)-3):
        window_closes = [
            df1h['close'].iloc[i-3],
            df1h['close'].iloc[i-2],
            df1h['close'].iloc[i-1],
            df1h['close'].iloc[i],
            df1h['close'].iloc[i+1],
            df1h['close'].iloc[i+2],
            df1h['close'].iloc[i+3]
        ]

        # 波谷：当前K线是窗口内最低点
        if df1h['close'].iloc[i] == min(window_closes):
            left_declining = window_closes[0] > window_closes[2]
            left_min_before = window_closes[2] >= window_closes[0]
            right_rising = window_closes[4] < window_closes[6]
            right_max_after = window_closes[4] <= window_closes[6]

            if left_declining and left_min_before and right_rising and right_max_after:
                valleys.append((i, df1h['close'].iloc[i]))

        # 波峰
        elif df1h['close'].iloc[i] == max(window_closes):
            left_rising = window_closes[0] < window_closes[2]
            left_max_before = window_closes[2] <= window_closes[0]
            right_declining = window_closes[4] > window_closes[6]
            right_min_after = window_closes[4] >= window_closes[6]

            if left_rising and left_max_before and right_declining and right_min_after:
                peaks.append((i, df1h['close'].iloc[i]))

    return peaks, valleys

print('='*70)
print('新算法检测结果（7根K线窗口V/U型）：')
print('='*70)

valleys, peaks = detect_peaks_valleys(df)
if valleys:
    print('\n波谷：')
    for idx, price in valleys:
        ts = df['timestamp'].iloc[idx] + pd.Timedelta(hours=8)
        print(f"  {ts.strftime('%m-%d %H:%M')} | ${price:.2f}")

    last_valley_idx, last_valley_price = valleys[-1]
    last_valley_time = df['timestamp'].iloc[last_valley_idx] + pd.Timedelta(hours=8)
    print(f'\n最后一个波谷: {last_valley_time.strftime("%m-%d %H:%M")} | ${last_valley_price:.2f}')
    print(f'当前价格: ${df["close"].iloc[-1]:.2f}')
