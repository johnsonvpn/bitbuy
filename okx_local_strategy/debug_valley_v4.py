#!/usr/bin/env python3
"""云端调试 - 检查1779附近的K线"""
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

print('K线数据（查找1779附近的时间点）：')
print('='*70)
for idx, row in df.iterrows():
    ts = row['timestamp'] + pd.Timedelta(hours=8)
    if 1775 <= row['close'] <= 1785 or 1795 <= row['close'] <= 1810:
        print(f"{ts.strftime('%m-%d %H:%M')} | 收盘: ${row['close']:.2f} | 最高: ${row['high']:.2f} | 最低: ${row['low']:.2f}")

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
        
        # 波峰
        elif df1h['close'].iloc[i] == max(window_closes):
            if window_closes[0] < window_closes[1] and window_closes[3] > window_closes[4]:
                peaks.append((i, df1h['close'].iloc[i]))
    
    return peaks, valleys

print('\n' + '='*70)
print('所有检测到的波谷（V/U型）：')
valleys, _ = detect_peaks_valleys(df)
for idx, price in valleys:
    ts = df['timestamp'].iloc[idx] + pd.Timedelta(hours=8)
    print(f"  {ts.strftime('%m-%d %H:%M')} | ${price:.2f}")

print('\n所有检测到的波峰（倒V/U型）：')
_, peaks = detect_peaks_valleys(df)
for idx, price in peaks:
    ts = df['timestamp'].iloc[idx] + pd.Timedelta(hours=8)
    print(f"  {ts.strftime('%m-%d %H:%M')} | ${price:.2f}")
