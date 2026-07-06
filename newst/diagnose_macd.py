#!/usr/bin/env python3
"""诊断脚本：检查OKX K线数据与MACD反转判断是否正确"""

import os
import sys
import ccxt
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv('binance_config.env')

SYMBOL = 'BTC-USDT-SWAP'
MACD_FAST = 9
MACD_SLOW = 21
MACD_SIGNAL = 60

exchange = ccxt.okx({
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET_KEY'),
    'password': os.getenv('OKX_PASSPHRASE'),
    'enableRateLimit': True,
    'options': {'defaultType': 'swap', 'marginMode': 'isolated'}
})

def fetch_klines(symbol, timeframe, limit=150):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = pd.to_numeric(df[col])
    return df

def calculate_macd_okx(prices):
    s = pd.Series(prices)
    ema_fast = s.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = s.ewm(span=MACD_SLOW, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=MACD_SIGNAL, adjust=False).mean()
    histogram = (macd_line - signal_line) * 2
    return histogram.values

def get_macd_trend(df, idx=-1):
    if df is None or len(df) < abs(idx) + 1 or 'macd_hist' not in df.columns:
        return None
    cur = df['macd_hist'].iloc[idx]
    prev = df['macd_hist'].iloc[idx - 1]
    if pd.isna(cur) or pd.isna(prev):
        return None
    return 'long' if cur > prev else 'short'

def diagnose():
    print("=" * 60)
    print("OKX K线与MACD反转诊断")
    print("=" * 60)

    df1h = fetch_klines(SYMBOL, '1h', 100)
    df1h['macd_hist'] = calculate_macd_okx(df1h['close'])

    print(f"\n获取到 {len(df1h)} 根1H K线")
    print(f"时间范围: {df1h['timestamp'].iloc[-1]} ~ {df1h['timestamp'].iloc[0]}")

    print("\n" + "=" * 60)
    print("最近10根K线详情:")
    print("=" * 60)
    print(f"{'时间(JST)':<22} {'收盘价':<12} {'MACD柱':<12} {'方向':<8} {'变化':<8}")
    print("-" * 60)

    for i in range(-1, -11, -1):
        ts = df1h['timestamp'].iloc[i]
        ts_jst = ts.tz_convert(timezone(timedelta(hours=9)))
        close = df1h['close'].iloc[i]
        macd = df1h['macd_hist'].iloc[i]
        trend = get_macd_trend(df1h, i)
        prev_trend = get_macd_trend(df1h, i - 1) if i > -10 else None

        change = "↑" if trend == 'long' else "↓"
        reversal = " [反转!]" if prev_trend and trend != prev_trend else ""

        print(f"{ts_jst.strftime('%Y-%m-%d %H:%M'):<22} {close:<12.2f} {macd:<12.4f} {trend:<8} {change}{reversal}")

    print("\n" + "=" * 60)
    print("反转检测分析:")
    print("=" * 60)

    # 检查最近几根K线的反转情况
    for i in range(-1, -6, -1):
        curr_trend = get_macd_trend(df1h, i)
        prev_trend = get_macd_trend(df1h, i - 1)
        prev_2_trend = get_macd_trend(df1h, i - 2)
        prev_3_trend = get_macd_trend(df1h, i - 3) if i > -8 else None

        ts = df1h['timestamp'].iloc[i].tz_convert(timezone(timedelta(hours=9)))

        reversal = prev_trend and curr_trend != prev_trend
        reversal_str = "✅ 反转" if reversal else "无反转"

        if reversal:
            # 修复后的追高检查逻辑
            if prev_2_trend and prev_3_trend:
                reversal_recent = (prev_trend != prev_2_trend) or (prev_2_trend != prev_3_trend and prev_trend == prev_2_trend)
                if reversal_recent:
                    print(f"  {ts.strftime('%H:%M')} 1H: {prev_trend}→{curr_trend} {reversal_str} ✅ 2根K线内，不追高")
                else:
                    print(f"  {ts.strftime('%H:%M')} 1H: {prev_trend}→{curr_trend} {reversal_str} ❌ 超过2根K线，追高")
            else:
                print(f"  {ts.strftime('%H:%M')} 1H: {prev_trend}→{curr_trend} {reversal_str}")

    print("\n" + "=" * 60)
    print("4H K线趋势:")
    print("=" * 60)
    df4h = fetch_klines(SYMBOL, '4h', 60)
    df4h['macd_hist'] = calculate_macd_okx(df4h['close'])

    for i in range(-1, -4, -1):
        ts = df4h['timestamp'].iloc[i].tz_convert(timezone(timedelta(hours=9)))
        trend = get_macd_trend(df4h, i)
        print(f"  {ts.strftime('%Y-%m-%d %H:%M')} 4H趋势: {trend}")

    print("\n" + "=" * 60)
    print("模拟开仓检查 (当前时刻):")
    print("=" * 60)

    current_1h = get_macd_trend(df1h, -1)
    prev_1h = get_macd_trend(df1h, -2)
    prev_2h = get_macd_trend(df1h, -3)
    prev_3h = get_macd_trend(df1h, -4)
    current_4h = get_macd_trend(df4h, -1)

    print(f"  1H当前方向: {current_1h}")
    print(f"  1H-1根前: {prev_1h}")
    print(f"  1H-2根前: {prev_2h}")
    print(f"  1H-3根前: {prev_3h}")
    print(f"  4H当前方向: {current_4h}")

    if prev_1h and current_1h != prev_1h:
        print(f"\n  ✅ 检测到1H反转: {prev_1h} → {current_1h}")
        reversal_recent = (prev_2h != current_1h) or (prev_3h != current_1h and prev_2h == current_1h)
        if reversal_recent:
            print(f"  ✅ 反转在2根K线内（不算追高）")
        else:
            print(f"  ❌ 反转超过2根K线（追高风险）")

        if current_4h == current_1h:
            print(f"  ✅ 4H({current_4h})与1H({current_1h})同向 → 可以开仓")
        else:
            print(f"  ❌ 4H({current_4h})与1H({current_1h})方向不一致 → 拒绝开仓")
    else:
        print(f"\n  ❌ 1H方向未反转，无开仓信号")

if __name__ == "__main__":
    diagnose()