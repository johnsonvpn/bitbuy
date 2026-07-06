#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
诊断脚本：检查OKX K线数据和波谷波峰检测
"""
import ccxt
import pandas as pd
import sys
import os

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import API_KEY, API_SECRET, PASSPHRASE

# 初始化交易所
exchange = ccxt.okx({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'password': PASSPHRASE,
    'enableRateLimit': True,
})

SYMBOL = 'ETH-USDT-SWAP'

print(f"=== 诊断 K线数据 {SYMBOL} ===\n")

# 获取1h K线
print("获取最近100根1h K线...")
ohlcv = exchange.fetch_ohlcv(SYMBOL, '1h', limit=100)
df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
print(f"获取到 {len(df)} 根K线\n")

# 计算MA和EMA
for window in [7, 25, 99]:
    df[f'ma{window}'] = df['close'].rolling(window=window).mean()
    df[f'ema{window}'] = df['close'].ewm(span=window, adjust=False).mean()

# 显示最近20根K线
print("=== 最近20根K线 ===")
for i in range(-20, 0):
    row = df.iloc[i]
    print(f"{row['timestamp'].strftime('%Y-%m-%d %H:%M')} | "
          f"O={row['open']:.2f} H={row['high']:.2f} L={row['low']:.2f} C={row['close']:.2f} | "
          f"MA7={row['ma7']:.2f} MA25={row['ma25']:.2f} MA99={row['ma99']:.2f}")

# 波峰波谷检测函数
def detect_peaks_valleys(df):
    """检测波峰和波谷"""
    peaks = []
    valleys = []

    for i in range(2, len(df) - 2):
        current = df['close'].iloc[i]
        prev2 = df['close'].iloc[i-2]
        prev1 = df['close'].iloc[i-1]
        next1 = df['close'].iloc[i+1]
        next2 = df['close'].iloc[i+2]

        # 波谷：中间值比前后都低
        if prev1 > current < next1 and prev2 > current < next2:
            valleys.append((i, current))
        # 波峰：中间值比前后都高
        elif prev1 < current > next1 and prev2 < current > next2:
            peaks.append((i, current))

    return peaks, valleys

# 检测波峰波谷
peaks, valleys = detect_peaks_valleys(df)

print(f"\n=== 检测到的波谷 ({len(valleys)}个) ===")
for idx, price in valleys[-10:]:
    timestamp = df.iloc[idx]['timestamp']
    print(f"{timestamp.strftime('%Y-%m-%d %H:%M')} | 价格: ${price:.2f}")

print(f"\n=== 检测到的波峰 ({len(peaks)}个) ===")
for idx, price in peaks[-10:]:
    timestamp = df.iloc[idx]['timestamp']
    print(f"{timestamp.strftime('%Y-%m-%d %H:%M')} | 价格: ${price:.2f}")

# 显示最后几个波谷
print(f"\n=== 最后3个波谷 ===")
for idx, price in valleys[-3:]:
    timestamp = df.iloc[idx]['timestamp']
    print(f"{timestamp.strftime('%Y-%m-%d %H:%M')} | 价格: ${price:.2f}")

# 检查当前价格是否站上/跌破均线
current_price = df['close'].iloc[-1]
print(f"\n=== 当前状态 ===")
print(f"当前价格: ${current_price:.2f}")
print(f"MA7: ${df['ma7'].iloc[-1]:.2f} | {'✅ 站上' if current_price > df['ma7'].iloc[-1] else '❌ 低于'}")
print(f"MA25: ${df['ma25'].iloc[-1]:.2f} | {'✅ 站上' if current_price > df['ma25'].iloc[-1] else '❌ 低于'}")
print(f"MA99: ${df['ma99'].iloc[-1]:.2f} | {'✅ 站上' if current_price > df['ma99'].iloc[-1] else '❌ 低于'}")
print(f"EMA7: ${df['ema7'].iloc[-1]:.2f} | {'✅ 站上' if current_price > df['ema7'].iloc[-1] else '❌ 低于'}")
print(f"EMA25: ${df['ema25'].iloc[-1]:.2f} | {'✅ 站上' if current_price > df['ema25'].iloc[-1] else '❌ 低于'}")
print(f"EMA99: ${df['ema99'].iloc[-1]:.2f} | {'✅ 站上' if current_price > df['ema99'].iloc[-1] else '❌ 低于'}")

# 检查12:00的K线数据
print(f"\n=== 12:00 UTC K线数据 ===")
kline_12 = df[df['timestamp'].dt.hour == 12].iloc[-1] if len(df[df['timestamp'].dt.hour == 12]) > 0 else None
if kline_12 is not None:
    print(f"时间: {kline_12['timestamp']}")
    print(f"开: {kline_12['open']:.2f} | 高: {kline_12['high']:.2f} | 低: {kline_12['low']:.2f} | 收: {kline_12['close']:.2f}")
