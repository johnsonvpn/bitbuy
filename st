import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ============ 【优化】策略参数 ============
# 根据交易记录分析调整参数，减少假信号

# 均线参数 - 调整为更平滑的周期
EMA_SHORT = 12          # 短期EMA (从9→12，减少噪音)
EMA_LONG = 26           # 长期EMA
EMA_SIGNAL = 9          # 信号线 (新增，用于确认趋势)

# RSI参数 - 收紧区间避免极端行情
RSI_PERIOD = 14
RSI_STRONG = 55         # 强势区 (从50→55)
RSI_WEAK = 45           # 弱势区 (从50→45)
RSI_OVERBOUGHT = 65     # 超买 (从70→65)
RSI_OVERSOLD = 35       # 超卖 (从30→35)

# 成交量参数 - 提高要求
VOLUME_MA_PERIOD = 20
VOLUME_THRESHOLD = 1.5  # 成交量倍数 (从1.2→1.5)

# 【新增】趋势强度过滤
MIN_EMA_DISTANCE = 0.5  # EMA最小距离(%)，避免横盘信号
MIN_TREND_BARS = 3      # 趋势至少持续3根K线

# 【新增】波动率过滤
ATR_PERIOD = 14
MIN_ATR_MULTIPLIER = 0.5  # ATR至少为价格的0.5%

# 【新增】时间过滤
AVOID_UTC_HOURS = [0, 1, 2, 3, 4, 5]  # 避开低流动性时段


# ============ 核心策略函数 ============
def generate_signal(data):
    """
    生成交易信号 (优化版)
    
    优化重点：
    1. 提高信号门槛，减少假突破
    2. 增加趋势确认，避免震荡市
    3. 加入时间和波动率过滤
    4. 多重确认机制
    
    返回: "buy" | "sell" | None
    """
    
    candles = data["candles"]
    
    # 至少需要60根K线计算指标
    if len(candles) < 60:
        return None
    
    # ========== 数据预处理 ==========
    df = pd.DataFrame(
        candles,
        columns=["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"]
    ).astype(float)
    
    df = df.iloc[::-1].reset_index(drop=True)
    
    # ========== 【优化】计算技术指标 ==========
    
    # 1. 三条EMA - 增加信号线确认
    df['ema_short'] = df['close'].ewm(span=EMA_SHORT, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=EMA_LONG, adjust=False).mean()
    df['ema_signal'] = df['close'].ewm(span=EMA_SIGNAL, adjust=False).mean()
    
    # 2. RSI
    df['rsi'] = calculate_rsi(df['close'], RSI_PERIOD)
    
    # 3. 成交量
    df['volume_ma'] = df['volume'].rolling(window=VOLUME_MA_PERIOD).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma']
    
    # 4. 【新增】ATR - 衡量波动率
    df['atr'] = calculate_atr(df, ATR_PERIOD)
    df['atr_pct'] = df['atr'] / df['close'] * 100
    
    # 5. 【新增】趋势强度
    df['ema_distance'] = abs(df['ema_short'] - df['ema_long']) / df['ema_long'] * 100
    
    # 6. 【新增】趋势方向 (连续几根K线)
    df['trend_direction'] = np.where(df['ema_short'] > df['ema_long'], 1, -1)
    df['trend_bars'] = df['trend_direction'].groupby(
        (df['trend_direction'] != df['trend_direction'].shift()).cumsum()
    ).cumcount() + 1
    
    # ========== 获取最新数据 ==========
    current = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]
    
    close = current['close']
    ema_short = current['ema_short']
    ema_long = current['ema_long']
    ema_signal = current['ema_signal']
    rsi = current['rsi']
    volume_ratio = current['volume_ratio']
    atr_pct = current['atr_pct']
    ema_distance = current['ema_distance']
    trend_bars = current['trend_bars']
    
    # ========== 【新增】过滤器检查 ==========
    
    # 过滤器1: 时间过滤
    current_hour = datetime.now(timezone.utc).hour
    if current_hour in AVOID_UTC_HOURS:
        print(f"⏰ 时间过滤: UTC {current_hour}点为低流动性时段")
        return None
    
    # 过滤器2: 波动率过滤
    if atr_pct < MIN_ATR_MULTIPLIER:
        print(f"📉 波动率过滤: ATR {atr_pct:.3f}% < {MIN_ATR_MULTIPLIER}%")
        return None
    
    # 过滤器3: 趋势强度过滤
    if ema_distance < MIN_EMA_DISTANCE:
        print(f"📊 趋势强度不足: EMA距离 {ema_distance:.2f}% < {MIN_EMA_DISTANCE}%")
        return None
    
    # ========== 打印调试信息 ==========
    print(f"\n{'='*70}")
    print(f"【策略信号检测】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
    print(f"价格: {close:.2f}")
    print(f"EMA{EMA_SHORT}: {ema_short:.2f} | EMA{EMA_LONG}: {ema_long:.2f} | 距离: {ema_distance:.2f}%")
    print(f"RSI: {rsi:.2f} | 成交量倍数: {volume_ratio:.2f}x | ATR: {atr_pct:.3f}%")
    print(f"趋势持续: {int(trend_bars)}根K线")
    
    # ========== 【优化】多重确认机制 ==========
    
    # 检测金叉/死叉
    golden_cross = (ema_short > ema_long) and (prev['ema_short'] <= prev['ema_long'])
    death_cross = (ema_short < ema_long) and (prev['ema_short'] >= prev['ema_long'])
    
    # 趋势延续 (已经有趋势，等待回踩/反弹)
    bullish_continuation = (ema_short > ema_long) and (close > ema_short) and (prev['close'] <= prev['ema_short'])
    bearish_continuation = (ema_short < ema_long) and (close < ema_short) and (prev['close'] >= prev['ema_short'])
    
    
    # ========== 【做多信号】==========
    if golden_cross or bullish_continuation:
        signal_type = "金叉" if golden_cross else "趋势延续"
        print(f"\n🟢 检测到 {signal_type}")
        
        # 做多条件检查
        conditions = {
            "1. 价格在快线上": close > ema_short,
            "2. 快线在信号线上": ema_short > ema_signal,
            "3. RSI强势区": RSI_STRONG < rsi < RSI_OVERBOUGHT,
            "4. 成交量放大": volume_ratio >= VOLUME_THRESHOLD,
            "5. 趋势足够强": ema_distance >= MIN_EMA_DISTANCE,
            "6. 趋势持续时间": trend_bars >= MIN_TREND_BARS if not golden_cross else True,
            "7. 波动率适中": atr_pct >= MIN_ATR_MULTIPLIER
        }
        
        print(f"\n【做多条件检查】")
        for name, result in conditions.items():
            status = "✓" if result else "✗"
            print(f"{status} {name}")
        
        # 计算信号强度
        passed = sum(conditions.values())
        total = len(conditions)
        strength = passed / total * 100
        
        print(f"\n信号强度: {passed}/{total} ({strength:.0f}%)")
        
        # 至少满足85%条件才发出信号
        if passed >= total * 0.85:
            print(f"\n🚀 【做多信号确认】强度: {strength:.0f}%")
            print(f"{'='*70}\n")
            return "buy"
        else:
            print(f"\n⚠️  信号强度不足 ({strength:.0f}% < 85%)")
            print(f"{'='*70}\n")
    
    
    # ========== 【做空信号】==========
    elif death_cross or bearish_continuation:
        signal_type = "死叉" if death_cross else "趋势延续"
        print(f"\n🔴 检测到 {signal_type}")
        
        # 做空条件检查
        conditions = {
            "1. 价格在快线下": close < ema_short,
            "2. 快线在信号线下": ema_short < ema_signal,
            "3. RSI弱势区": RSI_OVERSOLD < rsi < RSI_WEAK,
            "4. 成交量放大": volume_ratio >= VOLUME_THRESHOLD,
            "5. 趋势足够强": ema_distance >= MIN_EMA_DISTANCE,
            "6. 趋势持续时间": trend_bars >= MIN_TREND_BARS if not death_cross else True,
            "7. 波动率适中": atr_pct >= MIN_ATR_MULTIPLIER
        }
        
        print(f"\n【做空条件检查】")
        for name, result in conditions.items():
            status = "✓" if result else "✗"
            print(f"{status} {name}")
        
        # 计算信号强度
        passed = sum(conditions.values())
        total = len(conditions)
        strength = passed / total * 100
        
        print(f"\n信号强度: {passed}/{total} ({strength:.0f}%)")
        
        # 至少满足85%条件才发出信号
        if passed >= total * 0.85:
            print(f"\n📉 【做空信号确认】强度: {strength:.0f}%")
            print(f"{'='*70}\n")
            return "sell"
        else:
            print(f"\n⚠️  信号强度不足 ({strength:.0f}% < 85%)")
            print(f"{'='*70}\n")
    
    else:
        # 无交叉信号
        trend = "上升" if ema_short > ema_long else "下降"
        print(f"\n📊 当前{trend}趋势，无交易信号")
        print(f"{'='*70}\n")
    
    return None


# ============ 辅助函数 ============

def calculate_rsi(prices, period=14):
    """计算RSI指标"""
    delta = prices.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def calculate_atr(df, period=14):
    """
    计算ATR (Average True Range)
    用于衡量市场波动率
    """
    high = df['high']
    low = df['low']
    close = df['close'].shift(1)
    
    tr1 = high - low
    tr2 = abs(high - close)
    tr3 = abs(low - close)
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=period).mean()
    
    return atr


# ============ 【新增】信号质量评估 ============
def evaluate_signal_quality(df, signal_type):
    """
    评估信号质量 (可选，用于回测分析)
    
    返回: 质量分数 0-100
    """
    if signal_type not in ["buy", "sell"]:
        return 0
    
    current = df.iloc[-1]
    
    # 评分项
    score = 0
    
    # 1. 趋势强度 (30分)
    ema_distance = abs(current['ema_short'] - current['ema_long']) / current['ema_long'] * 100
    if ema_distance > 1.0:
        score += 30
    elif ema_distance > 0.5:
        score += 20
    elif ema_distance > 0.3:
        score += 10
    
    # 2. RSI位置 (25分)
    rsi = current['rsi']
    if signal_type == "buy":
        if 50 < rsi < 65:
            score += 25
        elif 45 < rsi < 70:
            score += 15
    else:  # sell
        if 35 < rsi < 50:
            score += 25
        elif 30 < rsi < 55:
            score += 15
    
    # 3. 成交量 (25分)
    volume_ratio = current['volume_ratio']
    if volume_ratio > 2.0:
        score += 25
    elif volume_ratio > 1.5:
        score += 20
    elif volume_ratio > 1.2:
        score += 10
    
    # 4. 波动率 (20分)
    atr_pct = current['atr_pct']
    if 0.5 < atr_pct < 2.0:
        score += 20
    elif 0.3 < atr_pct < 3.0:
        score += 10
    
    return score
