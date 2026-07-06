#!/usr/bin/env python3
"""本地测试波峰波谷检测"""
import sys
sys.path.insert(0, '/Users/johnsontang/work/bitbuy/okx_local_strategy')

import ccxt
from datetime import datetime
import os

# 加载环境变量
from dotenv import load_dotenv
load_dotenv('/Users/johnsontang/work/bitbuy/okx_local_strategy/okx_config_v2.env')

SYMBOL = 'ETH/USDT'

def fetch_klines(symbol, timeframe, limit=50):
    """获取K线数据"""
    exchange = ccxt.okx({
        'apiKey': os.getenv('OKX_API_KEY'),
        'secret': os.getenv('OKX_API_SECRET'),
        'password': os.getenv('OKX_PASSPHRASE'),
        'options': {'defaultType': 'swap'},
    })
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        import pandas as pd
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df
    except Exception as e:
        print(f"获取K线失败: {e}")
        return None

def detect_peaks_valleys(df1h):
    """检测1h K线的波峰和波谷"""
    if len(df1h) < 3:
        return None, None
    
    peaks = []
    valleys = []
    
    for i in range(1, len(df1h)-1):
        prev_close = df1h['close'].iloc[i-1]
        curr_close = df1h['close'].iloc[i]
        next_close = df1h['close'].iloc[i+1]
        
        # 波峰：当前收盘价高于前后收盘价
        if curr_close > prev_close and curr_close > next_close:
            peaks.append((i, curr_close))
        
        # 波谷：当前收盘价低于前后收盘价
        elif curr_close < prev_close and curr_close < next_close:
            valleys.append((i, curr_close))
    
    return peaks, valleys

# 获取最近50根1h K线
print(f"📡 获取 {SYMBOL} 最近50根1h K线...")
df1h = fetch_klines(SYMBOL, '1h', limit=50)

if df1h is not None:
    print(f"\n📊 K线数据（最近20根）：")
    print("=" * 80)
    
    # 显示最近20根K线
    for i, (timestamp, row) in enumerate(df1h.tail(20).iterrows()):
        utc_time = timestamp + pd.Timedelta(hours=8)  # 转北京时间
        print(f"{utc_time.strftime('%m-%d %H:%M')} | 收盘: ${row['close']:.2f} | 最高: ${row['high']:.2f} | 最低: ${row['low']:.2f}")
    
    print("\n" + "=" * 80)
    print("🔍 波峰波谷检测结果：")
    print("=" * 80)
    
    peaks, valleys = detect_peaks_valleys(df1h)
    
    print("\n📈 波峰（最近10个）：")
    if peaks:
        for idx, price in peaks[-10:]:
            timestamp = df1h.index[idx] + pd.Timedelta(hours=8)
            print(f"  {timestamp.strftime('%m-%d %H:%M')} | ${price:.2f}")
    else:
        print("  无波峰")
    
    print("\n📉 波谷（最近10个）：")
    if valleys:
        for idx, price in valleys[-10:]:
            timestamp = df1h.index[idx] + pd.Timedelta(hours=8)
            print(f"  {timestamp.strftime('%m-%d %H:%M')} | ${price:.2f}")
    else:
        print("  无波谷")
    
    # 获取最后一个波谷
    if valleys:
        last_valley_idx, last_valley_price = valleys[-1]
        last_valley_time = df1h.index[last_valley_idx] + pd.Timedelta(hours=8)
        print(f"\n🎯 最后一个波谷: {last_valley_time.strftime('%m-%d %H:%M')} | ${last_valley_price:.2f}")
        print(f"   当前价格: ${df1h['close'].iloc[-1]:.2f}")
        print(f"   当前价格是否 < 波谷: {df1h['close'].iloc[-1] < last_valley_price}")
