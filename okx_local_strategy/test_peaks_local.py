#!/usr/bin/env python3
"""
本地测试波峰波谷检测 - 与图表对比验证
"""

import sys
sys.path.insert(0, '.')
from okx_trading_bot_v2 import fetch_klines, detect_peaks_valleys, SYMBOL
import pandas as pd

# 获取最近100根1h K线
print(f"📡 获取 {SYMBOL} 最近100根1h K线...")
df1h = fetch_klines(SYMBOL, '1h', limit=100)
if df1h is None:
    print('❌ 获取K线失败')
    exit()

# 检测波峰波谷
peaks, valleys = detect_peaks_valleys(df1h)

# 打印详细K线表格
print()
print('='*90)
print(f'📊 {SYMBOL} 1h K线波峰波谷分析')
print('='*90)
print()
print('时间'.ljust(22) + '开盘'.rjust(10) + '最高'.rjust(10) + '最低'.rjust(10) + '收盘'.rjust(10) + '振幅'.rjust(8) + '类型'.ljust(10))
print('-'*90)

for i in range(len(df1h)):
    row = df1h.iloc[i]
    time_str = str(row.name)[:19]  # 只显示到秒
    is_peak = any(p[0] == i for p in peaks)
    is_valley = any(v[0] == i for v in valleys)
    
    pattern = ''
    if is_peak:
        pattern = '🔺 波峰'
    elif is_valley:
        pattern = '🔻 波谷'
    
    range_pct = ((row['high'] - row['low']) / row['open']) * 100
    
    print(f'{time_str.ljust(22)} {row["open"]:>10.2f} {row["high"]:>10.2f} {row["low"]:>10.2f} {row["close"]:>10.2f} {range_pct:>7.2f}% {pattern.ljust(10)}')

print()
print('='*90)
print('📍 检测到的波峰:')
print('-'*45)
if peaks:
    for idx, price in peaks:
        time_str = str(df1h.index[idx])[:19]
        print(f'  {time_str} → 💰 ${price:.2f}')
else:
    print('  未检测到波峰')

print()
print('📍 检测到的波谷:')
print('-'*45)
if valleys:
    for idx, price in valleys:
        time_str = str(df1h.index[idx])[:19]
        print(f'  {time_str} → 💰 ${price:.2f}')
else:
    print('  未检测到波谷')

print()
print('='*90)
print('💡 使用说明:')
print('  1. 在TradingView或OKX上打开ETH-USDT 1h图表')
print('  2. 对比上述时间点的K线是否确实是波峰/波谷')
print('  3. 波峰: 该K线最高价 > 左右相邻K线的最高价')
print('  4. 波谷: 该K线最低价 < 左右相邻K线的最低价')
print('='*90)
