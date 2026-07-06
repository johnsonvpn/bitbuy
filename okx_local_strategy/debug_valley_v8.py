#!/usr/bin/env python3
"""云端调试 - 测试使用<代替==的V/U型算法"""
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

def detect_peaks_valleys(df1h):
    if len(df1h) < 7:
        return None, None

    peaks = []
    valleys = []

    for i in range(3, len(df1h)-3):
        curr_close = df1h['close'].iloc[i]

        window_closes = [
            df1h['close'].iloc[i-3],
            df1h['close'].iloc[i-2],
            df1h['close'].iloc[i-1],
            curr_close,
            df1h['close'].iloc[i+1],
            df1h['close'].iloc[i+2],
            df1h['close'].iloc[i+3]
        ]

        # 波谷：当前K线严格小于窗口内所有其他K线
        if curr_close < min(window_closes[:3]) and curr_close < min(window_closes[4:]):
            left_min = min(window_closes[0], window_closes[1], window_closes[2])
            right_min = min(window_closes[4], window_closes[5], window_closes[6])
            if curr_close < left_min and curr_close < right_min:
                valleys.append((i, curr_close))

        # 波峰：当前K线严格大于窗口内所有其他K线
        elif curr_close > max(window_closes[:3]) and curr_close > max(window_closes[4:]):
            left_max = max(window_closes[0], window_closes[1], window_closes[2])
            right_max = max(window_closes[4], window_closes[5], window_closes[6])
            if curr_close > left_max and curr_close > right_max:
                peaks.append((i, curr_close))

    return peaks, valleys

print('='*70)
print('新算法检测结果（使用<代替==）：')
print('='*70)

peaks, valleys = detect_peaks_valleys(df)
print('\n波谷：')
for idx, price in valleys:
    ts = df['timestamp'].iloc[idx] + pd.Timedelta(hours=8)
    print(f"  {ts.strftime('%m-%d %H:%M')} | ${price:.2f}")

print('\n波峰：')
for idx, price in peaks:
    ts = df['timestamp'].iloc[idx] + pd.Timedelta(hours=8)
    print(f"  {ts.strftime('%m-%d %H:%M')} | ${price:.2f}")

if valleys:
    last_idx, last_price = valleys[-1]
    ts = df['timestamp'].iloc[last_idx] + pd.Timedelta(hours=8)
    print(f'\n最后一个波谷: {ts.strftime("%m-%d %H:%M")} | ${last_price:.2f}')
    print(f'当前价格: ${df["close"].iloc[-1]:.2f}')
