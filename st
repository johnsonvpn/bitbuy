

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ============ 策略参数 ============
# 这些参数可以根据回测结果调整

EMA_SHORT = 9           # 短期EMA周期
EMA_LONG = 26           # 长期EMA周期
RSI_PERIOD = 14         # RSI周期
RSI_OVERBOUGHT = 70     # RSI超买线
RSI_OVERSOLD = 30       # RSI超卖线
VOLUME_MA_PERIOD = 20   # 成交量均线周期
VOLUME_THRESHOLD = 1.2  # 成交量阈值 (1.2倍均值)

# ============ 核心策略函数 ============
def generate_signal(data):
    """
    生成交易信号
    
    参数:
        data: 包含K线数据的字典 {"price": float, "candles": list}
    
    返回:
        "buy": 做多信号
        "sell": 做空信号
        None: 无信号
    """
    
    candles = data["candles"]
    
    # 至少需要50根K线才能计算指标
    if len(candles) < 50:
        return None
    
    # ========== 数据预处理 ==========
    df = pd.DataFrame(
        candles,
        columns=["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"]
    ).astype(float)
    
    # OKX返回的数据是从新到旧,需要反转
    df = df.iloc[::-1].reset_index(drop=True)
    
    # ========== 计算技术指标 ==========
    
    # 1. EMA均线
    df['ema_short'] = df['close'].ewm(span=EMA_SHORT, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=EMA_LONG, adjust=False).mean()
    
    # 2. RSI指标
    df['rsi'] = calculate_rsi(df['close'], RSI_PERIOD)
    
    # 3. 成交量均线
    df['volume_ma'] = df['volume'].rolling(window=VOLUME_MA_PERIOD).mean()
    
    # ========== 获取最新数据 ==========
    current = df.iloc[-1]      # 当前K线
    previous = df.iloc[-2]     # 前一根K线
    
    close_price = current['close']
    ema_short = current['ema_short']
    ema_long = current['ema_long']
    ema_short_prev = previous['ema_short']
    ema_long_prev = previous['ema_long']
    rsi = current['rsi']
    volume = current['volume']
    volume_ma = current['volume_ma']
    
    # ========== 打印调试信息 ==========
    print(f"\n{'='*60}")
    print(f"【策略调试信息】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    print(f"价格: {close_price:.2f}")
    print(f"EMA{EMA_SHORT}: {ema_short:.2f} | EMA{EMA_LONG}: {ema_long:.2f}")
    print(f"RSI: {rsi:.2f}")
    print(f"成交量: {volume:.2f} | 均量: {volume_ma:.2f}")
    print(f"成交量倍数: {(volume/volume_ma):.2f}x")
    
    # ========== 检测均线交叉 ==========
    golden_cross = (ema_short > ema_long) and (ema_short_prev <= ema_long_prev)
    death_cross = (ema_short < ema_long) and (ema_short_prev >= ema_long_prev)
    
    if golden_cross:
        print(f"🟢 检测到金叉!")
    if death_cross:
        print(f"🔴 检测到死叉!")
    
    # ========== 做多信号 ==========
    if golden_cross:
        # 条件1: 金叉
        print(f"✓ 条件1: 金叉 (EMA{EMA_SHORT}上穿EMA{EMA_LONG})")
        
        # 条件2: 价格在EMA短期上方
        price_above_ema = close_price > ema_short
        print(f"{'✓' if price_above_ema else '✗'} 条件2: 价格在EMA{EMA_SHORT}上方 ({close_price:.2f} > {ema_short:.2f})")
        
        # 条件3: RSI在强势区
        rsi_strong = rsi > 50
        print(f"{'✓' if rsi_strong else '✗'} 条件3: RSI在强势区 ({rsi:.2f} > 50)")
        
        # 条件4: 成交量放大
        volume_confirm = volume > volume_ma * VOLUME_THRESHOLD
        print(f"{'✓' if volume_confirm else '✗'} 条件4: 成交量放大 ({volume/volume_ma:.2f}x > {VOLUME_THRESHOLD}x)")
        
        # 条件5: 避免超买
        not_overbought = rsi < RSI_OVERBOUGHT
        print(f"{'✓' if not_overbought else '✗'} 条件5: 未超买 (RSI {rsi:.2f} < {RSI_OVERBOUGHT})")
        
        if price_above_ema and rsi_strong and volume_confirm and not_overbought:
            print(f"\n🚀 【做多信号】所有条件满足!")
            print(f"{'='*60}\n")
            return "buy"
        else:
            print(f"\n⚠️  金叉出现但过滤条件未满足,不开仓")
            print(f"{'='*60}\n")
    
    # ========== 做空信号 ==========
    elif death_cross:
        # 条件1: 死叉
        print(f"✓ 条件1: 死叉 (EMA{EMA_SHORT}下穿EMA{EMA_LONG})")
        
        # 条件2: 价格在EMA短期下方
        price_below_ema = close_price < ema_short
        print(f"{'✓' if price_below_ema else '✗'} 条件2: 价格在EMA{EMA_SHORT}下方 ({close_price:.2f} < {ema_short:.2f})")
        
        # 条件3: RSI在弱势区
        rsi_weak = rsi < 50
        print(f"{'✓' if rsi_weak else '✗'} 条件3: RSI在弱势区 ({rsi:.2f} < 50)")
        
        # 条件4: 成交量放大
        volume_confirm = volume > volume_ma * VOLUME_THRESHOLD
        print(f"{'✓' if volume_confirm else '✗'} 条件4: 成交量放大 ({volume/volume_ma:.2f}x > {VOLUME_THRESHOLD}x)")
        
        # 条件5: 避免超卖
        not_oversold = rsi > RSI_OVERSOLD
        print(f"{'✓' if not_oversold else '✗'} 条件5: 未超卖 (RSI {rsi:.2f} > {RSI_OVERSOLD})")
        
        if price_below_ema and rsi_weak and volume_confirm and not_oversold:
            print(f"\n📉 【做空信号】所有条件满足!")
            print(f"{'='*60}\n")
            return "sell"
        else:
            print(f"\n⚠️  死叉出现但过滤条件未满足,不开仓")
            print(f"{'='*60}\n")
    
    else:
        # 无交叉信号
        trend = "上升" if ema_short > ema_long else "下降"
        print(f"📊 当前趋势: {trend} (无交叉信号)")
        print(f"{'='*60}\n")
    
    return None


# ============ 辅助函数 ============
def calculate_rsi(prices, period=14):
    """
    计算RSI指标
    
    参数:
        prices: 价格序列 (pandas Series)
        period: RSI周期
    
    返回:
        RSI值序列
    """
    delta = prices.diff()
    
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi

