#!/usr/bin/env python3
"""
获取指定日期范围的 MACD 数据用于对照分析
"""
import sys
sys.path.insert(0, '/app')

import pandas as pd
from datetime import datetime, timezone, timedelta
import ccxt
from dotenv import load_dotenv
import os

# 加载配置
load_dotenv('/app/binance_config.env')
api_key = os.getenv('BINANCE_API_KEY')
api_secret = os.getenv('BINANCE_API_SECRET')

# 初始化交易所
exchange = ccxt.binance({
    'apiKey': api_key,
    'secret': api_secret,
    'options': {'defaultType': 'future'}
})

# 设置时区
tz_cst = timezone(timedelta(hours=8))

# 获取昨天的时间范围
today = datetime.now(tz_cst).date()
yesterday = today - timedelta(days=1)
start_time = datetime.combine(yesterday, datetime.min.time()).replace(tzinfo=tz_cst)
end_time = datetime.combine(today, datetime.min.time()).replace(tzinfo=tz_cst)

print(f"=" * 80)
print(f"获取数据范围: {yesterday} (北京时间)")
print(f"=" * 80)

# 交易对
symbol = "BTC/USDT:USDT"

# 通用获取K线函数
def fetch_klines_data(exch, sym, timeframe, limit):
    since = None
    all_klines = []
    max_retries = 3

    for retry in range(max_retries):
        try:
            klines = exch.fetch_ohlcv(sym, timeframe, since, limit)
            all_klines.extend(klines)
            if len(klines) < limit:
                break
            since = klines[-1][0] + 1
        except Exception as e:
            if retry == max_retries - 1:
                raise e
            continue
    return all_klines

# 通用MACD计算
def calculate_macd(prices, fast=9, slow=21, signal=60):
    s = pd.Series(prices)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist

# 获取 1H 数据
print(f"\n{'='*80}")
print(f"1H MACD 数据")
print(f"{'='*80}")
try:
    klines_1h = fetch_klines_data(exchange, symbol, '1h', 100)
    df1h = pd.DataFrame(klines_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    # 转换时间戳为北京时间
    df1h['timestamp'] = pd.to_datetime(df1h['timestamp'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')

    df1h['dif'], df1h['dea'], df1h['hist'] = calculate_macd(df1h['close'].values)

    # 过滤昨天的数据
    df1h_filtered = df1h[(df1h['timestamp'] >= start_time) & (df1h['timestamp'] < end_time)]

    print(f"\n{'时间(CST)':<25} {'DIF':>14} {'DEA':>14} {'HIST':>14} {'判断(hist)':<12} {'判断(dif)':<10}")
    print("-" * 110)
    for _, row in df1h_filtered.iterrows():
        kline_time = row['timestamp'].strftime("%Y-%m-%d %H:%M")
        dif = row['dif']
        dea = row['dea']
        hist = row['hist']
        trend_hist = 'long' if hist > 0 else 'short'
        trend_dif = 'long' if dif > 0 else 'short'
        print(f"{kline_time:<25} {dif:>14.4f} {dea:>14.4f} {hist:>14.4f} {trend_hist:<12} {trend_dif:<10}")
except Exception as e:
    print(f"获取1H数据失败: {e}")
    import traceback
    traceback.print_exc()

# 获取 4H 数据
print(f"\n{'='*80}")
print(f"4H MACD 数据")
print(f"{'='*80}")
try:
    klines_4h = fetch_klines_data(exchange, symbol, '4h', 100)
    df4h = pd.DataFrame(klines_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    # 转换时间戳为北京时间
    df4h['timestamp'] = pd.to_datetime(df4h['timestamp'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')

    df4h['dif'], df4h['dea'], df4h['hist'] = calculate_macd(df4h['close'].values)

    # 过滤昨天的数据
    df4h_filtered = df4h[(df4h['timestamp'] >= start_time) & (df4h['timestamp'] < end_time)]

    print(f"\n{'时间(CST)':<25} {'DIF':>14} {'DEA':>14} {'HIST':>14} {'判断(hist)':<12} {'判断(dif)':<10}")
    print("-" * 110)
    for _, row in df4h_filtered.iterrows():
        kline_time = row['timestamp'].strftime("%Y-%m-%d %H:%M")
        dif = row['dif']
        dea = row['dea']
        hist = row['hist']
        trend_hist = 'long' if hist > 0 else 'short'
        trend_dif = 'long' if dif > 0 else 'short'
        print(f"{kline_time:<25} {dif:>14.4f} {dea:>14.4f} {hist:>14.4f} {trend_hist:<12} {trend_dif:<10}")
except Exception as e:
    print(f"获取4H数据失败: {e}")
    import traceback
    traceback.print_exc()

print(f"\n{'='*80}")
print(f"MACD 趋势判断说明:")
print(f"{'='*80}")
print(f"当前判断逻辑: hist > 0 → long, hist < 0 → short")
print(f"备选判断逻辑: dif > 0 → long, dif < 0 → short (更接近图形)")
