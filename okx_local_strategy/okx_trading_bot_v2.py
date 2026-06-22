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
import requests
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

# ==================== Telegram 配置 ====================
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

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

# 合约面值缓存
_contract_size_cache = {}

# ==================== 辅助函数 ====================
def get_contract_size() -> float:
    """获取合约面值"""
    if 'size' in _contract_size_cache:
        return _contract_size_cache['size']
    try:
        log.info(f"正在加载市场信息，获取合约面值...")
        markets = exchange.load_markets()
        key = SYMBOL.replace('-', '/').replace('USDT', 'USDT:USDT', 1)
        market = markets.get(key) or markets.get(SYMBOL)
        
        if market:
            size = float(market['contractSize'])
        else:
            log.warning(f"未找到市场信息，使用默认合约面值")
            if 'BTC' in SYMBOL:
                size = 0.01
            elif 'SOL' in SYMBOL:
                size = 1.0
            elif 'ETH' in SYMBOL:
                size = 0.1
            else:
                size = 0.01
        
        _contract_size_cache['size'] = size
        log.info(f"合约面值: {size}")
        return size
    except Exception as e:
        log.error(f"获取合约面值失败: {e}")
        if 'ETH' in SYMBOL:
            return 0.1
        return 1.0

def set_leverage_once():
    """设置杠杆（只设置一次）"""
    try:
        exchange.set_leverage(LEVERAGE, SYMBOL, params={'mgnMode': 'isolated'})
        log.info(f"杠杆已设置: {LEVERAGE}x 逐仓")
    except Exception as e:
        log.warning(f"设置杠杆失败（可能已设置）: {e}")

def now_local():
    return datetime.utcnow() + timedelta(hours=8)

def now_str():
    return now_local().strftime('%Y-%m-%d %H:%M:%S')

def send_telegram_message(message):
    """发送 Telegram 消息"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.debug("Telegram 配置未设置，跳过推送")
        return
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            log.info("✅ Telegram 消息发送成功")
        else:
            log.warning(f"❌ Telegram 消息发送失败: {response.text}")
    except Exception as e:
        log.error(f"❌ Telegram 消息发送异常: {e}")

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

def fetch_klines(symbol, timeframe, limit=100, retries=3):
    """获取K线数据（带重试机制）"""
    for attempt in range(retries):
        try:
            log.debug(f"正在获取K线数据: {symbol} {timeframe} limit={limit}")
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df = df.set_index('timestamp')
            log.debug(f"K线数据获取成功: {len(df)} 条")
            return df
        except Exception as e:
            log.warning(f"获取K线数据失败 (尝试 {attempt+1}/{retries}): {str(e)[:200]}")
            if attempt < retries - 1:
                wait_time = (attempt + 1) * 3  # 递增等待时间：3秒、6秒、9秒
                log.info(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                log.error(f"获取K线数据最终失败: {str(e)[:200]}")
                return None

def detect_peaks_valleys(df1h):
    """检测1h K线的波峰和波谷
    基于V/U字型形态判断：
    - 波谷：连续几根K线形成V型或U型，中间的最低点
    - 波峰：连续几根K线形成倒V型或倒U型，中间的最高点

    使用7根K线窗口（V/U型检测）：
    - 当前K线是窗口内最低点 → 波谷
    - 当前K线是窗口内最高点 → 波峰
    - 不要求严格的左右对称
    """
    if len(df1h) < 7:
        return None, None

    peaks = []  # (索引, 价格) 列表
    valleys = []  # (索引, 价格) 列表

    # 使用7根K线滑动窗口检测V/U型形态
    # 窗口: [i-3, i-2, i-1, i, i+1, i+2, i+3]
    for i in range(3, len(df1h)-3):
        curr_close = df1h['close'].iloc[i]

        # 获取7根K线窗口的收盘价
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
            # 确保左右两侧都比当前K线高
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

def get_daily_close_direction():
    """获取日线收盘价方向（使用已完成的K线）
    返回: (昨天方向, 前天方向)
    """
    try:
        df_daily = fetch_klines(SYMBOL, '1d', limit=10)
        if df_daily is None or len(df_daily) < 3:
            log.warning("无法获取足够日K线数据（需要至少3根）")
            return None, None

        yesterday_close = df_daily['close'].iloc[-2]
        day_before_close = df_daily['close'].iloc[-3]

        yesterday_direction = 'long' if yesterday_close > day_before_close else 'short'
        day_before_direction = 'long' if day_before_close > df_daily['close'].iloc[-4] else 'short'

        log.info(f"📅 日线收盘方向: 前天={day_before_direction}({day_before_close:.2f}) | 昨天={yesterday_direction}({yesterday_close:.2f})")

        return yesterday_direction, day_before_direction

    except Exception as e:
        log.error(f"获取日线收盘方向失败: {e}")
        return None, None

def is_above_all_ma(df1h, verbose=False):
    """检查1h K线是否站上所有均线"""
    details = []
    if len(df1h) < 1:
        if verbose:
            details.append("数据不足")
        return False, details
    
    current_price = df1h['close'].iloc[-1]
    all_above = True
    
    # 检查所有MA均线
    for window in MA_WINDOWS:
        ma_col = f'ma{window}'
        ema_col = f'ema{window}'
        if ma_col not in df1h.columns or ema_col not in df1h.columns:
            if verbose:
                details.append(f"缺少均线数据: {ma_col} 或 {ema_col}")
            return False, details
        
        ma_value = df1h[ma_col].iloc[-1]
        ema_value = df1h[ema_col].iloc[-1]
        
        if current_price > ma_value and current_price > ema_value:
            if verbose:
                details.append(f"MA{window} = ${ma_value:.2f} ✅ | EMA{window} = ${ema_value:.2f} ✅")
        else:
            all_above = False
            if verbose:
                details.append(f"MA{window} = ${ma_value:.2f} ❌ | EMA{window} = ${ema_value:.2f} ❌")
    
    return all_above, details

def is_below_all_ma(df1h, verbose=False):
    """检查1h K线是否跌破所有均线"""
    details = []
    if len(df1h) < 1:
        if verbose:
            details.append("数据不足")
        return False, details
    
    current_price = df1h['close'].iloc[-1]
    all_below = True
    
    # 检查所有MA均线
    for window in MA_WINDOWS:
        ma_col = f'ma{window}'
        ema_col = f'ema{window}'
        if ma_col not in df1h.columns or ema_col not in df1h.columns:
            if verbose:
                details.append(f"缺少均线数据: {ma_col} 或 {ema_col}")
            return False, details
        
        ma_value = df1h[ma_col].iloc[-1]
        ema_value = df1h[ema_col].iloc[-1]
        
        if current_price < ma_value and current_price < ema_value:
            if verbose:
                details.append(f"MA{window} = ${ma_value:.2f} ✅ | EMA{window} = ${ema_value:.2f} ✅")
        else:
            all_below = False
            if verbose:
                details.append(f"MA{window} = ${ma_value:.2f} ❌ | EMA{window} = ${ema_value:.2f} ❌")
    
    return all_below, details

# ==================== 交易函数 ====================
def open_position(direction, price, stop_loss):
    """开仓"""
    try:
        set_leverage_once()
        
        # 计算数量（考虑合约面值）
        contract_size = get_contract_size()
        position_value = TARGET_MARGIN * LEVERAGE
        per_contract_value = price * contract_size
        quantity = position_value / per_contract_value
        quantity = round(quantity, 4)
        
        # 确保数量满足最小精度要求
        min_qty = 0.01  # OKX最低数量精度
        if quantity < min_qty:
            log.warning(f"计算数量 {quantity} 低于最小精度 {min_qty}，调整为 {min_qty}")
            quantity = min_qty
        
        if stop_loss:
            log.info(f"⬆️ [{now_str()}] 开仓 {direction.upper()} | 张数: {quantity} | 当前价: ${price:.2f} | 初始止损: ${stop_loss:.2f}")
        else:
            log.info(f"⬆️ [{now_str()}] 开仓 {direction.upper()} | 张数: {quantity} | 当前价: ${price:.2f} | 初始止损: 未设置")
        
        # 下单（OKX需要指定posSide参数）
        params = {'tdMode': 'isolated', 'posSide': 'long' if direction == 'long' else 'short'}
        if direction == 'long':
            order = exchange.create_order(SYMBOL, 'market', 'buy', quantity, None, params)
        else:
            order = exchange.create_order(SYMBOL, 'market', 'sell', quantity, None, params)
        
        fill_price = order.get('average') or order.get('price') or price
        log.info(f"✅ [{now_str()}] 开仓成功 | 成交均价: ${fill_price:.2f} | 订单ID: {order['id']}")
        
        # 发送 Telegram 通知
        tg_message = f"🚀 **开仓成功**\n\n" \
                     f"📅 时间: {now_str()}\n" \
                     f"📈 方向: {direction.upper()}\n" \
                     f"💰 成交均价: ${fill_price:.2f}\n" \
                     f"📊 数量: {quantity} 张\n" \
                     f"🛡️ 止损: ${stop_loss:.2f}" if stop_loss else f"🚀 **开仓成功**\n\n" \
                     f"📅 时间: {now_str()}\n" \
                     f"📈 方向: {direction.upper()}\n" \
                     f"💰 成交均价: ${fill_price:.2f}\n" \
                     f"📊 数量: {quantity} 张\n" \
                     f"🛡️ 止损: 未设置"
        send_telegram_message(tg_message)
        
        # 更新状态
        position_tracker['position'] = direction
        position_tracker['entry_price'] = fill_price
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
        
        # 获取当前持仓（过滤掉历史记录，只保留有持仓的）
        positions = exchange.fetch_positions([SYMBOL])
        active_position = None
        for pos in positions:
            contracts = float(pos.get('contracts', 0))
            if contracts != 0:
                active_position = pos
                break
        
        if not active_position:
            log.warning("无持仓可平")
            # 更新状态
            position_tracker['position'] = None
            position_tracker['entry_price'] = 0
            position_tracker['entry_time'] = None
            position_tracker['tracking_valley'] = None
            position_tracker['tracking_peak'] = None
            save_state()
            return True
        
        side = active_position['side']
        quantity = abs(float(active_position['contracts']))
        pos_side = active_position['info']['posSide']
        log.info(f"检测到持仓: {side} {quantity}")
        
        # 平仓（OKX需要指定posSide、tdMode和reduceOnly参数）
        order_side = 'sell' if side == 'long' else 'buy'
        params = {
            'tdMode': 'isolated',
            'posSide': pos_side,
            'reduceOnly': True
        }
        order = exchange.create_order(SYMBOL, 'market', order_side, quantity, None, params)
        
        # 计算盈亏
        order_id = order.get('id')
        exit_price = order.get('average') or order.get('price') or 0
        
        if exit_price == 0 and order_id:
            log.info(f"等待1秒后重新查询订单...")
            time.sleep(1)
            try:
                order_info = exchange.fetch_order(order_id, SYMBOL)
                exit_price = order_info.get('average') or order_info.get('price') or 0
            except Exception as e:
                log.warning(f"重新查询订单失败: {e}")
        
        if exit_price == 0:
            log.info(f"使用ticker当前价作为参考")
            try:
                ticker = exchange.fetch_ticker(SYMBOL)
                exit_price = float(ticker.get('last', 0))
            except Exception as e:
                log.warning(f"获取ticker失败: {e}")
        
        entry_price = float(active_position['entryPrice'])
        contract_size = get_contract_size()
        if side == 'long':
            profit = (exit_price - entry_price) * quantity * contract_size
        else:
            profit = (entry_price - exit_price) * quantity * contract_size
        
        log.info(f"✅ [{now_str()}] 平仓成功 | 成交均价: ${exit_price:.2f} | 订单ID: {order_id}")
        log.info(f"💰 本次交易 {'盈利' if profit >= 0 else '亏损'}: ${profit:.2f} | 开仓价: ${entry_price:.2f} | 平仓价: ${exit_price:.2f}")
        
        # 发送 Telegram 通知
        tg_message = f"📉 **平仓成功**\n\n" \
                     f"📅 时间: {now_str()}\n" \
                     f"📈 方向: {side.upper()}\n" \
                     f"💰 开仓价: ${entry_price:.2f}\n" \
                     f"💵 平仓价: ${exit_price:.2f}\n" \
                     f"📊 结果: {'✅ 盈利' if profit >= 0 else '❌ 亏损'} ${profit:.2f}"
        send_telegram_message(tg_message)
        
        # 更新状态
        position_tracker['position'] = None
        position_tracker['entry_price'] = 0
        position_tracker['entry_time'] = None
        position_tracker['tracking_valley'] = None
        position_tracker['tracking_peak'] = None
        position_tracker['trailing_stop'] = None
        position_tracker['max_profit_pct'] = 0
        position_tracker['last_trade_loss'] = (profit < 0)
        position_tracker['total_profit'] += profit
        position_tracker['trade_count'] += 1
        
        save_state()
        return True
    except Exception as e:
        log.error(f"平仓失败: {e}", exc_info=True)
        return False

# ==================== 核心策略逻辑 ====================
def check_open_condition(df1h):
    """检查开仓条件"""
    if position_tracker.get('position'):
        msg = f"🔒 [{now_str()}] 开仓检查 | 交易对: {SYMBOL}\n当前有持仓 {position_tracker.get('position').upper()}，跳过开仓检查"
        log.info(msg)
        send_telegram_message(msg)
        return None
    
    current_price = df1h['close'].iloc[-1]
    log.info(f"\n📊 开仓条件检查 | 当前价格: ${current_price:.2f}")
    
    # 检查是否站上所有均线（做多）
    above_all_ma, ma_details = is_above_all_ma(df1h, verbose=True)
    if above_all_ma:
        msg = f"✅ [{now_str()}] 开仓检查 | 交易对: {SYMBOL}\n开多条件满足: 1h K线站上所有均线\n当前价格: ${current_price:.2f}"
        log.info(f"✅ 开多条件满足: 1h K线站上所有均线")
        for detail in ma_details:
            log.info(f"   - {detail}")
            msg += f"\n{detail}"
        send_telegram_message(msg)
        return 'long'
    else:
        msg = f"❌ [{now_str()}] 开仓检查 | 交易对: {SYMBOL}\n开多条件未满足\n当前价格: ${current_price:.2f}"
        log.info(f"❌ 开多条件未满足")
        if ma_details:
            for detail in ma_details:
                log.info(f"   - {detail}")
                msg += f"\n{detail}"
        send_telegram_message(msg)
    
    # 检查是否跌破所有均线（做空）
    below_all_ma, ma_details = is_below_all_ma(df1h, verbose=True)
    if below_all_ma:
        msg = f"✅ [{now_str()}] 开仓检查 | 交易对: {SYMBOL}\n开空条件满足: 1h K线跌破所有均线\n当前价格: ${current_price:.2f}"
        log.info(f"✅ 开空条件满足: 1h K线跌破所有均线")
        for detail in ma_details:
            log.info(f"   - {detail}")
            msg += f"\n{detail}"
        send_telegram_message(msg)
        return 'short'
    else:
        msg = f"❌ [{now_str()}] 开仓检查 | 交易对: {SYMBOL}\n开空条件未满足\n当前价格: ${current_price:.2f}"
        log.info(f"❌ 开空条件未满足")
        if ma_details:
            for detail in ma_details:
                log.info(f"   - {detail}")
                msg += f"\n{detail}"
        send_telegram_message(msg)
    
    return None

def check_close_condition(df1h):
    """检查平仓条件"""
    position = position_tracker.get('position')
    if not position:
        msg = f"🔒 [{now_str()}] 平仓检查 | 交易对: {SYMBOL}\n当前无持仓，跳过平仓检查"
        log.info(msg)
        send_telegram_message(msg)
        return False
    
    current_price = df1h['close'].iloc[-1]
    entry_price = position_tracker.get('entry_price')
    
    # 计算当前盈利百分比
    if position == 'long':
        profit_pct = ((current_price - entry_price) / entry_price) * 100
    else:
        profit_pct = ((entry_price - current_price) / entry_price) * 100
    
    # 更新最高盈利记录
    max_profit_pct = position_tracker.get('max_profit_pct', 0)
    if profit_pct > max_profit_pct:
        position_tracker['max_profit_pct'] = profit_pct
        save_state()
        max_profit_pct = profit_pct
    
    log.info(f"\n🔍 平仓条件检查 | 当前持仓: {position} | 当前价格: ${current_price:.2f}")
    log.info(f"   - 开仓价: ${entry_price:.2f} | 当前盈利: {profit_pct:.2f}% | 最高盈利: {max_profit_pct:.2f}%")
    
    tg_message = f"🔍 [{now_str()}] 平仓检查 | 交易对: {SYMBOL}\n"
    tg_message += f"📈 方向: {position.upper()} | 当前价格: ${current_price:.2f}\n"
    tg_message += f"💰 开仓价: ${entry_price:.2f} | 当前盈利: {profit_pct:.2f}% | 最高盈利: {max_profit_pct:.2f}%\n"
    
    if position == 'long':
        # 多单：跟踪波谷，跌破波谷平仓
        current_valley = get_last_valley(df1h)
        tracking_valley = position_tracker.get('tracking_valley')
        trailing_stop = position_tracker.get('trailing_stop')
        
        log.info(f"   - 当前波谷: ${current_valley:.2f}" if current_valley else "   - 当前波谷: 未检测到")
        log.info(f"   - 跟踪波谷: ${tracking_valley:.2f}" if tracking_valley else "   - 跟踪波谷: 未设置")
        log.info(f"   - 移动止损: ${trailing_stop:.2f}" if trailing_stop else "   - 移动止损: 未设置")
        
        tg_message += f"📍 当前波谷: ${current_valley:.2f}" if current_valley else "📍 当前波谷: 未检测到\n"
        tg_message += f"🎯 跟踪波谷: ${tracking_valley:.2f}" if tracking_valley else "🎯 跟踪波谷: 未设置\n"
        tg_message += f"🛡️ 移动止损: ${trailing_stop:.2f}" if trailing_stop else "🛡️ 移动止损: 未设置\n"
        
        # 移动止损逻辑：盈利超过10%时启动
        if max_profit_pct >= 10:
            if trailing_stop is None:
                # 首次启动移动止损，锁定10%利润
                trailing_stop_price = entry_price * (1 + 0.10)
                position_tracker['trailing_stop'] = trailing_stop_price
                save_state()
                log.info(f"🛡️ 启动移动止损: 锁定10%利润 @ ${trailing_stop_price:.2f}")
                tg_message += f"✅ 启动移动止损: 锁定10%利润 @ ${trailing_stop_price:.2f}\n"
            else:
                # 检查是否触发移动止损
                if current_price < trailing_stop:
                    log.info(f"🛡️ 移动止损触发: 当前价格 ${current_price:.2f} < 移动止损 ${trailing_stop:.2f}")
                    log.info(f"   - 锁定利润: 10.00% | 回撤: {max_profit_pct - 10:.2f}%")
                    tg_message += f"🛡️ 平仓原因: 移动止损触发\n"
                    tg_message += f"   - 当前价格 ${current_price:.2f} < 移动止损 ${trailing_stop:.2f}\n"
                    tg_message += f"   - 锁定利润: 10.00% | 回撤: {max_profit_pct - 10:.2f}%\n"
                    send_telegram_message(tg_message)
                    return True
        
        if current_valley:
            if tracking_valley is None:
                # 首次设置波谷
                position_tracker['tracking_valley'] = current_valley
                log.info(f"✅ 首次记录波谷: ${current_valley:.2f}")
                save_state()
                tg_message += f"✅ 首次记录波谷: ${current_valley:.2f}\n"
                tg_message += f"✅ 不平仓原因: 等待波谷确认\n"
            else:
                # 检查是否跌破波谷
                if tracking_valley and current_price < tracking_valley:
                    log.info(f"❌ 平仓条件满足: 当前价格 ${current_price:.2f} < 跟踪波谷 ${tracking_valley:.2f}")
                    log.info(f"   - 价格跌破幅度: {(tracking_valley - current_price):.2f} USDT ({((tracking_valley - current_price)/tracking_valley*100):.2f}%)")
                    tg_message += f"❌ 平仓原因: 价格跌破波谷\n"
                    tg_message += f"   - 当前价格 ${current_price:.2f} < 跟踪波谷 ${tracking_valley:.2f}\n"
                    tg_message += f"   - 跌破幅度: {(tracking_valley - current_price):.2f} USDT ({((tracking_valley - current_price)/tracking_valley*100):.2f}%)\n"
                    send_telegram_message(tg_message)
                    return True
                
                # 更新波谷（只更新更高的波谷，锁定利润）
                if current_valley > tracking_valley:
                    position_tracker['tracking_valley'] = current_valley
                    log.info(f"🔼 更新波谷: ${tracking_valley:.2f} → ${current_valley:.2f}")
                    save_state()
                    tracking_valley = current_valley
                    tg_message += f"🔼 更新波谷: ${tracking_valley:.2f} → ${current_valley:.2f}\n"
                
                tg_message += f"✅ 不平仓原因: 当前价格 ${current_price:.2f} >= 跟踪波谷 ${tracking_valley:.2f}\n"
        
        else:
            tg_message += f"⚠️ 不平仓原因: 未检测到波谷\n"
        
    else:  # position == 'short'
        # 空单：跟踪波峰，突破波峰平仓
        current_peak = get_last_peak(df1h)
        tracking_peak = position_tracker.get('tracking_peak')
        trailing_stop = position_tracker.get('trailing_stop')
        
        log.info(f"   - 当前波峰: ${current_peak:.2f}" if current_peak else "   - 当前波峰: 未检测到")
        log.info(f"   - 跟踪波峰: ${tracking_peak:.2f}" if tracking_peak else "   - 跟踪波峰: 未设置")
        log.info(f"   - 移动止损: ${trailing_stop:.2f}" if trailing_stop else "   - 移动止损: 未设置")
        
        tg_message += f"📍 当前波峰: ${current_peak:.2f}" if current_peak else "📍 当前波峰: 未检测到\n"
        tg_message += f"🎯 跟踪波峰: ${tracking_peak:.2f}" if tracking_peak else "🎯 跟踪波峰: 未设置\n"
        tg_message += f"🛡️ 移动止损: ${trailing_stop:.2f}" if trailing_stop else "🛡️ 移动止损: 未设置\n"
        
        # 移动止损逻辑：盈利超过10%时启动
        if max_profit_pct >= 10:
            if trailing_stop is None:
                # 首次启动移动止损，锁定10%利润
                trailing_stop_price = entry_price * (1 - 0.10)
                position_tracker['trailing_stop'] = trailing_stop_price
                save_state()
                log.info(f"🛡️ 启动移动止损: 锁定10%利润 @ ${trailing_stop_price:.2f}")
                tg_message += f"✅ 启动移动止损: 锁定10%利润 @ ${trailing_stop_price:.2f}\n"
            else:
                # 检查是否触发移动止损
                if current_price > trailing_stop:
                    log.info(f"🛡️ 移动止损触发: 当前价格 ${current_price:.2f} > 移动止损 ${trailing_stop:.2f}")
                    log.info(f"   - 锁定利润: 10.00% | 回撤: {max_profit_pct - 10:.2f}%")
                    tg_message += f"🛡️ 平仓原因: 移动止损触发\n"
                    tg_message += f"   - 当前价格 ${current_price:.2f} > 移动止损 ${trailing_stop:.2f}\n"
                    tg_message += f"   - 锁定利润: 10.00% | 回撤: {max_profit_pct - 10:.2f}%\n"
                    send_telegram_message(tg_message)
                    return True
        
        if current_peak:
            if tracking_peak is None:
                # 首次设置波峰
                position_tracker['tracking_peak'] = current_peak
                log.info(f"✅ 首次记录波峰: ${current_peak:.2f}")
                save_state()
                tg_message += f"✅ 首次记录波峰: ${current_peak:.2f}\n"
                tg_message += f"✅ 不平仓原因: 等待波峰确认\n"
            else:
                # 检查是否突破波峰
                if tracking_peak and current_price > tracking_peak:
                    log.info(f"❌ 平仓条件满足: 当前价格 ${current_price:.2f} > 跟踪波峰 ${tracking_peak:.2f}")
                    log.info(f"   - 价格突破幅度: {(current_price - tracking_peak):.2f} USDT ({((current_price - tracking_peak)/tracking_peak*100):.2f}%)")
                    tg_message += f"❌ 平仓原因: 价格突破波峰\n"
                    tg_message += f"   - 当前价格 ${current_price:.2f} > 跟踪波峰 ${tracking_peak:.2f}\n"
                    tg_message += f"   - 突破幅度: {(current_price - tracking_peak):.2f} USDT ({((current_price - tracking_peak)/tracking_peak*100):.2f}%)\n"
                    send_telegram_message(tg_message)
                    return True
                
                # 更新波峰（只更新更低的波峰，锁定利润）
                if current_peak < tracking_peak:
                    position_tracker['tracking_peak'] = current_peak
                    log.info(f"🔽 更新波峰: ${tracking_peak:.2f} → ${current_peak:.2f}")
                    save_state()
                    tracking_peak = current_peak
                    tg_message += f"🔽 更新波峰: ${tracking_peak:.2f} → ${current_peak:.2f}\n"
                
                tg_message += f"✅ 不平仓原因: 当前价格 ${current_price:.2f} <= 跟踪波峰 ${tracking_peak:.2f}\n"
        
        else:
            tg_message += f"⚠️ 不平仓原因: 未检测到波峰\n"
    
    send_telegram_message(tg_message)
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
    
    # 记录上次检查的小时，避免重复检查
    last_checked_hour = -1
    check_executed = False  # 标记该小时是否已检查过
    
    while True:
        try:
            now = now_local()
            h = now.hour
            m = now.minute
            s = now.second
            
            # 每小时检查一次（在整点后10-15秒，与策略1错开）
            is_new_hour = h != last_checked_hour
            
            # 如果是新的小时，重置标记
            if is_new_hour:
                check_executed = False
                last_checked_hour = h
            
            is_hourly_check = m == 0 and s >= 10 and s <= 15 and not check_executed
            
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
                    # 获取日线方向（使用昨天已完成的K线）
                    daily_current_dir, _ = get_daily_close_direction()

                    # 如果持仓方向与日线方向相反，立即平仓
                    if daily_current_dir:
                        if (position == 'short' and daily_current_dir == 'long') or \
                           (position == 'long' and daily_current_dir == 'short'):
                            log.warning(f"🚨 日线方向与持仓相反: 持仓={position} | 日线方向={daily_current_dir} | 立即平仓!")
                            if close_position('日线方向相反平仓'):
                                check_executed = True
                                time.sleep(1)
                                continue

                    if check_close_condition(df1h):
                        if close_position('跌破波谷/突破波峰'):
                            log.info(f"⏳ 平仓成功，等待下一周期检查开仓条件，避免同周期内频繁交易")
                            check_executed = True
                            time.sleep(1)
                            continue
                
                # 如果无持仓，检查开仓条件
                else:
                    # 获取日线方向（使用昨天已完成的K线）
                    daily_current_dir, _ = get_daily_close_direction()
                    
                    open_signal = check_open_condition(df1h)
                    if open_signal:
                        # 根据日线方向过滤开仓信号
                        if daily_current_dir == 'long' and open_signal == 'long':
                            log.info(f"📗 日线多头: 开多仓信号确认")
                            stop_loss = get_last_valley(df1h)
                            if stop_loss:
                                open_position(open_signal, current_price, stop_loss)
                            else:
                                log.warning(f"❌ 未找到初始止损点（波谷），放弃开仓")
                        elif daily_current_dir == 'short' and open_signal == 'short':
                            log.info(f"📕 日线空头: 开空仓信号确认")
                            stop_loss = get_last_peak(df1h)
                            if stop_loss:
                                open_position(open_signal, current_price, stop_loss)
                            else:
                                log.warning(f"❌ 未找到初始止损点（波峰），放弃开仓")
                        elif daily_current_dir:
                            log.info(f"🚫 日线方向={daily_current_dir}，过滤反向信号={open_signal}")
                        else:
                            log.info(f"⏳ 日线方向不明，等待明确信号")
                
                # 标记该小时检查已完成
                check_executed = True
            
            time.sleep(1)
        
        except Exception as e:
            log.error(f"主循环异常: {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()
