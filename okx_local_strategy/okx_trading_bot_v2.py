#!/usr/bin/env python3
"""
OKX 本地版交易策略机器人 - 波峰波谷策略

核心逻辑:
  [04:30] 检查1h K线是否站上/跌破所有均线 → 开仓
  开仓后跟踪波峰波谷 → 跌破波谷(多单)/突破波峰(空单) → 平仓

策略参数:
  - 均线: MA9, MA21, MA60, EMA9, EMA21, EMA60
  - 杠杆: 7x 逐仓
"""

import os
import json
import logging
import time
import ccxt
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from pathlib import Path

load_dotenv('okx_config_v2.env')

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('bot_v2.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ==================== 策略配置 ====================
SYMBOL          = 'ETH-USDT-SWAP'
TARGET_MARGIN   = 20        # 目标保证金（USDT）
LEVERAGE        = 7

# 均线参数
MA_WINDOWS = [9, 21, 60]

STATE_FILE      = Path('state_v2.json')

# ==================== 交易所连接 ====================
exchange = ccxt.okx({
    'apiKey':    os.getenv('OKX_API_KEY'),
    'secret':    os.getenv('OKX_SECRET_KEY'),
    'password':  os.getenv('OKX_PASSPHRASE'),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',
    }
})

# ==================== 全局状态 ====================
position_tracker = {
    'position': None,           # 当前持仓: 'long' | 'short' | None
    'entry_price': 0,           # 开仓价格
    'entry_time': None,         # 开仓时间
    'tracking_valley': None,    # 跟踪的波谷价格（多单）
    'tracking_peak': None,      # 跟踪的波峰价格（空单）
    'last_trade_loss': False,   # 上次交易是否亏损
    'total_profit': 0,          # 总盈亏
    'trade_count': 0,           # 交易次数
}

# ==================== 辅助函数 ====================
def now_utc():
    return datetime.now(timezone.utc)

def now_str():
    return now_utc().strftime('%Y-%m-%d %H:%M:%S')

def save_state():
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(position_tracker, f, indent=2)
    except Exception as e:
        log.error(f"保存状态失败: {e}")

def load_state():
    global position_tracker
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                position_tracker = json.load(f)
                log.info(f"状态已加载: position={position_tracker.get('position')}")
        except Exception as e:
            log.error(f"加载状态失败: {e}")
    else:
        log.info("未找到状态文件，使用默认配置")

def calculate_indicators(df, timeframe):
    """计算均线指标"""
    min_required = max(MA_WINDOWS)
    if len(df) < min_required:
        log.warning(f"数据点不足 [{timeframe}]: 当前{len(df)}根, 需要至少{min_required}根")
        return df

    # 计算MA均线
    for window in MA_WINDOWS:
        df[f'ma{window}'] = df['close'].rolling(window=window).mean()
        df[f'ema{window}'] = df['close'].ewm(span=window, adjust=True).mean()
    
    return df

def fetch_klines(symbol, timeframe, limit=100):
    """获取K线数据"""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = df.set_index('timestamp')
        return df
    except Exception as e:
        log.error(f"获取K线数据失败: {e}")
        return None

def detect_peaks_valleys(df1h):
    """检测1h K线的波峰和波谷"""
    if len(df1h) < 3:
        return None, None
    
    # 波峰：当前K线收盘价高于前后K线
    peaks = []
    # 波谷：当前K线收盘价低于前后K线
    valleys = []
    
    for i in range(1, len(df1h)-1):
        prev_close = df1h['close'].iloc[i-1]
        curr_close = df1h['close'].iloc[i]
        next_close = df1h['close'].iloc[i+1]
        
        if curr_close > prev_close and curr_close > next_close:
            peaks.append((i, curr_close))
        elif curr_close < prev_close and curr_close < next_close:
            valleys.append((i, curr_close))
    
    return peaks, valleys

def get_last_valley(df1h):
    """获取上一个波谷"""
    peaks, valleys = detect_peaks_valleys(df1h)
    if valleys:
        return valleys[-1][1]  # 返回最后一个波谷的价格
    return None

def get_last_peak(df1h):
    """获取上一个波峰"""
    peaks, valleys = detect_peaks_valleys(df1h)
    if peaks:
        return peaks[-1][1]  # 返回最后一个波峰的价格
    return None

def is_above_all_ma(df1h):
    """检查1h K线是否站上所有均线"""
    if len(df1h) < 1:
        return False
    
    current_price = df1h['close'].iloc[-1]
    
    # 检查所有MA均线
    for window in MA_WINDOWS:
        ma_col = f'ma{window}'
        ema_col = f'ema{window}'
        if ma_col not in df1h.columns or ema_col not in df1h.columns:
            return False
        if current_price <= df1h[ma_col].iloc[-1] or current_price <= df1h[ema_col].iloc[-1]:
            return False
    
    return True

def is_below_all_ma(df1h):
    """检查1h K线是否跌破所有均线"""
    if len(df1h) < 1:
        return False
    
    current_price = df1h['close'].iloc[-1]
    
    # 检查所有MA均线
    for window in MA_WINDOWS:
        ma_col = f'ma{window}'
        ema_col = f'ema{window}'
        if ma_col not in df1h.columns or ema_col not in df1h.columns:
            return False
        if current_price >= df1h[ma_col].iloc[-1] or current_price >= df1h[ema_col].iloc[-1]:
            return False
    
    return True

# ==================== 交易函数 ====================
def open_position(direction, price, stop_loss):
    """开仓"""
    try:
        log.info(f"⬆️ [{now_str()}] 开仓 {direction.upper()} | 当前价: ${price:.2f} | 初始止损: ${stop_loss:.2f if stop_loss else 0}")
        
        # 计算数量
        contract_value = TARGET_MARGIN * LEVERAGE
        quantity = contract_value / price
        quantity = round(quantity, 4)
        
        # 下单
        if direction == 'long':
            order = exchange.create_order(SYMBOL, 'market', 'buy', quantity)
        else:
            order = exchange.create_order(SYMBOL, 'market', 'sell', quantity)
        
        log.info(f"✅ [{now_str()}] 开仓成功 | 成交均价: ${price:.2f} | 订单ID: {order['id']}")
        
        # 更新状态
        position_tracker['position'] = direction
        position_tracker['entry_price'] = price
        position_tracker['entry_time'] = now_str()
        
        # 初始止损点设置（即您说的：止损点为上一个波谷/波峰）
        if direction == 'long':
            position_tracker['tracking_valley'] = stop_loss
            position_tracker['tracking_peak'] = None
        else:
            position_tracker['tracking_peak'] = stop_loss
            position_tracker['tracking_valley'] = None
        
        save_state()
        return True
    except Exception as e:
        log.error(f"开仓失败: {e}")
        return False

def close_position(reason):
    """平仓"""
    try:
        log.info(f"⬇️ [{now_str()}] 执行平仓 | 原因: {reason}")
        
        # 获取当前持仓
        positions = exchange.fetch_positions([SYMBOL])
        if not positions:
            log.warning("无持仓可平")
            return True
        
        position = positions[0]
        side = position['side']
        quantity = abs(float(position['contracts']))
        
        # 平仓
        if side == 'long':
            order = exchange.create_order(SYMBOL, 'market', 'sell', quantity)
        else:
            order = exchange.create_order(SYMBOL, 'market', 'buy', quantity)
        
        # 计算盈亏
        entry_price = float(position['entryPrice'])
        exit_price = float(order['average'])
        margin = float(position['initialMargin'])
        
        if side == 'long':
            profit = (exit_price - entry_price) / entry_price * margin * LEVERAGE
        else:
            profit = (entry_price - exit_price) / entry_price * margin * LEVERAGE
        
        log.info(f"✅ [{now_str()}] 平仓成功 | 成交均价: ${exit_price:.2f} | 订单ID: {order['id']}")
        log.info(f"💰 本次交易 {'盈利' if profit >= 0 else '亏损'}: ${profit:.2f} | 开仓价: ${entry_price:.2f} | 平仓价: ${exit_price:.2f}")
        
        # 更新状态
        position_tracker['position'] = None
        position_tracker['entry_price'] = 0
        position_tracker['entry_time'] = None
        position_tracker['tracking_valley'] = None
        position_tracker['tracking_peak'] = None
        position_tracker['last_trade_loss'] = (profit < 0)
        position_tracker['total_profit'] += profit
        position_tracker['trade_count'] += 1
        
        save_state()
        return True
    except Exception as e:
        log.error(f"平仓失败: {e}")
        return False

# ==================== 核心策略逻辑 ====================
def check_open_condition(df1h):
    """检查开仓条件"""
    if position_tracker.get('position'):
        return None
    
    # 检查是否站上所有均线（做多）
    if is_above_all_ma(df1h):
        log.info(f"✅ 1h K线站上所有均线，触发开多信号")
        return 'long'
    
    # 检查是否跌破所有均线（做空）
    if is_below_all_ma(df1h):
        log.info(f"✅ 1h K线跌破所有均线，触发开空信号")
        return 'short'
    
    return None

def check_close_condition(df1h):
    """检查平仓条件"""
    position = position_tracker.get('position')
    if not position:
        return False
    
    current_price = df1h['close'].iloc[-1]
    
    if position == 'long':
        # 多单：跟踪波谷，跌破波谷平仓
        current_valley = get_last_valley(df1h)
        
        if current_valley:
            tracking_valley = position_tracker.get('tracking_valley')
            
            if tracking_valley is None:
                # 首次设置波谷
                position_tracker['tracking_valley'] = current_valley
                log.info(f"📌 首次记录波谷: ${current_valley:.2f}")
                save_state()
            else:
                # 更新波谷（只更新更高的波谷）
                if current_valley > tracking_valley:
                    position_tracker['tracking_valley'] = current_valley
                    log.info(f"📈 更新波谷: ${tracking_valley:.2f} → ${current_valley:.2f}")
                    save_state()
                
                # 检查是否跌破波谷
                if current_price < tracking_valley:
                    log.info(f"❌ 跌破波谷 ${tracking_valley:.2f}，触发平仓")
                    return True
        
    else:  # position == 'short'
        # 空单：跟踪波峰，突破波峰平仓
        current_peak = get_last_peak(df1h)
        
        if current_peak:
            tracking_peak = position_tracker.get('tracking_peak')
            
            if tracking_peak is None:
                # 首次设置波峰
                position_tracker['tracking_peak'] = current_peak
                log.info(f"📌 首次记录波峰: ${current_peak:.2f}")
                save_state()
            else:
                # 更新波峰（只更新更低的波峰）
                if current_peak < tracking_peak:
                    position_tracker['tracking_peak'] = current_peak
                    log.info(f"📉 更新波峰: ${tracking_peak:.2f} → ${current_peak:.2f}")
                    save_state()
                
                # 检查是否突破波峰
                if current_price > tracking_peak:
                    log.info(f"❌ 突破波峰 ${tracking_peak:.2f}，触发平仓")
                    return True
    
    return False

# ==================== 主循环 ====================
def main():
    log.info("=== OKX 波峰波谷策略机器人 已启动 ===")
    
    # 测试连接
    log.info("🔍 测试API连接...")
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        log.info(f"✅ 价格获取成功: {SYMBOL} = {ticker['last']} USDT")
        
        positions = exchange.fetch_positions([SYMBOL])
        log.info(f"✅ 持仓获取成功: {len(positions)} 个持仓")
        
        balance = exchange.fetch_balance()
        log.info(f"✅ 账户信息获取成功: USDT余额 = {balance['USDT']['total']:.2f}")
    except Exception as e:
        log.error(f"API连接失败: {e}")
        return
    
    # 加载状态
    load_state()
    
    log.info(f"标的: {SYMBOL} | 目标保证金: {TARGET_MARGIN} USDT | 杠杆: {LEVERAGE}x")
    log.info(f"入场: 1h K线站上/跌破所有均线 | 出场: 跌破波谷(多单)/突破波峰(空单)")
    
    while True:
        try:
            now = now_utc()
            m = now.minute
            s = now.second
            
            # 每小时检查一次（在整点时刻）
            is_hourly_check = m == 0 and s >= 0 and s <= 5
            
            if is_hourly_check:
                # 获取1h K线
                df1h = fetch_klines(SYMBOL, '1h', limit=100)
                if df1h is None:
                    time.sleep(1)
                    continue
                
                df1h = calculate_indicators(df1h, '1h')
                
                # 打印当前状态
                current_price = df1h['close'].iloc[-1]
                position = position_tracker.get('position')
                log.info(f"\n[{now_str()}] 策略检查")
                log.info(f"1h价格: ${current_price:.2f}")
                log.info(f"持仓={position or '无'}")
                
                # 如果有持仓，先检查平仓条件
                if position:
                    if check_close_condition(df1h):
                        if close_position('跌破波谷/突破波峰'):
                            # 平仓后立即检查开仓条件
                            open_signal = check_open_condition(df1h)
                            if open_signal:
                                open_position(open_signal, current_price, '波峰波谷策略')
                
                # 如果无持仓，检查开仓条件
                else:
                    open_signal = check_open_condition(df1h)
                    if open_signal:
                        # 获取初始止损点（上一个波谷/波峰）
                        stop_loss = None
                        if open_signal == 'long':
                            stop_loss = get_last_valley(df1h)
                        else:
                            stop_loss = get_last_peak(df1h)
                        
                        if stop_loss:
                            open_position(open_signal, current_price, stop_loss)
                        else:
                            log.warning(f"❌ 未找到初始止损点，放弃开仓")
            
            time.sleep(1)
        
        except Exception as e:
            log.error(f"主循环异常: {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()
