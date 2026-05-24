#!/usr/bin/env python3
"""
OKX 本地版交易策略机器人 - 5min MACD反转开仓 + 30min MACD平仓

核心逻辑:
  [04:30] 检测5min MACD柱趋势是否反转 → 反转且无持仓 → 开仓
  [29:55] 判断30min MACD方向是否与开仓时5min方向不同 → 不同 → 平仓
  开仓后等待下一次5min反转才能再次开仓（自然防重入，无需开关）

策略参数:
  - MACD: fast=9, slow=21, signal=60
  - RSI: length=9, overbought=70, oversold=30
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

load_dotenv('okx_config.env')

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ==================== 策略配置 ====================
SYMBOL          = 'SOL-USDT-SWAP'  # OKX格式
TARGET_MARGIN   = 20        # 目标保证金（USDT）
LEVERAGE        = 7
RSI_LENGTH      = 9

MACD_FAST       = 9
MACD_SLOW       = 21
MACD_SIGNAL     = 60

RSI_OVERBOUGHT  = 70    # RSI超买阈值（开多信号时过滤）
RSI_OVERSOLD    = 30    # RSI超卖阈值（开空信号时过滤）

# ==================== 时间周期配置 ====================
TREND_TIMEFRAME    = '12h'   # 大趋势时间周期（用于趋势判断、平仓、过滤）
ENTRY_TIMEFRAME    = '30m'   # 小趋势/入场时间周期（用于开仓信号）

# 推荐配置组合：
# 组合1（稳健型）：TREND_TIMEFRAME='12h', ENTRY_TIMEFRAME='30m'
# 组合2（平衡型）：TREND_TIMEFRAME='8h', ENTRY_TIMEFRAME='15m'  
# 组合3（激进型）：TREND_TIMEFRAME='4h', ENTRY_TIMEFRAME='5m'

def parse_timeframe_minutes(timeframe: str) -> int:
    """解析时间周期为分钟数"""
    timeframe = timeframe.lower()
    if timeframe.endswith('m'):
        return int(timeframe[:-1])
    elif timeframe.endswith('h'):
        return int(timeframe[:-1]) * 60
    elif timeframe.endswith('d'):
        return int(timeframe[:-1]) * 1440
    else:
        return 60  # 默认1小时

# ==================== 动态止盈止损配置 ====================
STOP_LOSS_RATIO          = 0.02      # 止损比例 (2%)
TAKE_PROFIT_RATIO        = 0.05      # 止盈比例 (5%)
TRAILING_STOP_ENABLE     = True      # 是否启用移动止损
TRAILING_STOP_RATIO      = 0.015     # 移动止损追踪比例 (1.5%)
TRAILING_STOP_START_RATIO = 0.03     # 触发移动止损的最小盈利比例 (3%)
TRAILING_CHECK_INTERVAL  = 5         # 移动止损检查间隔（秒）

STATE_FILE      = Path('state.json')

# ==================== 交易所连接 ====================
exchange = ccxt.okx({
    'apiKey':    os.getenv('OKX_API_KEY'),
    'secret':    os.getenv('OKX_SECRET_KEY'),
    'password':  os.getenv('OKX_PASSPHRASE'),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',
        'marginMode':  'isolated',
    }
})

# ==================== 状态持久化 ====================
_DEFAULT_TRACKER = {
    'entry_price':              None,
    'entry_time':               None,
    'position':                 None,
    'entry_entry_macd_direction': None,
    'last_entry_macd_direction':  None,
    'open_contracts':            None,
    'trade_history':             [],
    'last_trade_loss':           False,
    # 止盈止损相关字段
    'stop_loss_price':          None,
    'take_profit_price':        None,
    'trailing_stop_price':      None,
    'max_profit_price':         None,
    'min_profit_price':         None,
}

def _serialize(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out

def _deserialize(d: dict) -> dict:
    for k in ('entry_time',):
        if d.get(k):
            try:
                d[k] = datetime.fromisoformat(d[k])
            except Exception:
                d[k] = None
    return d

def save_state():
    try:
        STATE_FILE.write_text(
            json.dumps(_serialize(position_tracker), ensure_ascii=False, indent=2)
        )
    except Exception as e:
        log.warning(f"保存状态失败: {e}")

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            # 处理字段名兼容性：从旧字段名迁移到新字段名
            if 'last_5m_macd_direction' in data and 'last_entry_macd_direction' not in data:
                data['last_entry_macd_direction'] = data['last_5m_macd_direction']
            if 'entry_5m_macd_direction' in data and 'entry_entry_macd_direction' not in data:
                data['entry_entry_macd_direction'] = data['entry_5m_macd_direction']
            # 使用默认值填充缺失的字段
            for key, default_value in _DEFAULT_TRACKER.items():
                if key not in data:
                    data[key] = default_value
            return _deserialize(data)
        except Exception as e:
            log.warning(f"加载状态失败，使用默认值: {e}")
    return dict(_DEFAULT_TRACKER)

position_tracker = load_state()

# ==================== 时区工具 ====================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def now_cst() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))

def cst_str() -> str:
    return now_cst().strftime("%Y-%m-%d %H:%M:%S")

# ==================== 合约信息 ====================
_contract_size_cache: dict = {}

def get_contract_size() -> float:
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
    except Exception as e:
        log.error(f"获取合约面值失败: {e}", exc_info=True)
        if 'BTC' in SYMBOL:
            size = 0.01
        elif 'SOL' in SYMBOL:
            size = 1.0
        elif 'ETH' in SYMBOL:
            size = 0.1
        else:
            size = 0.01
    _contract_size_cache['size'] = size
    base_coin = SYMBOL.split('-')[0] if '-' in SYMBOL else 'BTC'
    log.info(f"最终使用的合约面值: {size} {base_coin}/张")
    return size

def calculate_contracts(current_price: float) -> float:
    contract_size = get_contract_size()
    position_value = TARGET_MARGIN * LEVERAGE
    per_contract_value = current_price * contract_size
    contracts = position_value / per_contract_value
    contracts = round(contracts, 4)
    
    base_coin = SYMBOL.split('-')[0] if '-' in SYMBOL else 'BTC'
    log.info(f"开仓计算: 保证金={TARGET_MARGIN} USDT, 杠杆={LEVERAGE}x, 合约价值={position_value} USDT")
    log.info(f"合约面值={contract_size} {base_coin}/张, 当前价=${current_price:.2f}, 下单张数={contracts:.4f}")
    
    return contracts

# ==================== 下单执行 ====================
def set_leverage_once():
    try:
        exchange.set_leverage(LEVERAGE, SYMBOL, params={'mgnMode': 'isolated'})
        log.info(f"杠杆已设置: {LEVERAGE}x 逐仓")
    except Exception as e:
        log.warning(f"设置杠杆失败（可能已设置）: {e}")

def calculate_stop_loss_take_profit(entry_price: float, direction: str):
    if direction == 'long':
        stop_loss = entry_price * (1 - STOP_LOSS_RATIO)
        take_profit = entry_price * (1 + TAKE_PROFIT_RATIO)
        trailing_start_price = entry_price * (1 + TRAILING_STOP_START_RATIO)
    else:
        stop_loss = entry_price * (1 + STOP_LOSS_RATIO)
        take_profit = entry_price * (1 - TAKE_PROFIT_RATIO)
        trailing_start_price = entry_price * (1 - TRAILING_STOP_START_RATIO)
    return stop_loss, take_profit, trailing_start_price

def open_position(direction: str, current_price: float, entry_5m_direction: str) -> bool:
    try:
        set_leverage_once()
        contracts = calculate_contracts(current_price)
        side = 'buy' if direction == 'long' else 'sell'
        pos_side = 'long' if direction == 'long' else 'short'

        log.info(f"⬆️  [{cst_str()}] 开仓 {direction.upper()} | 张数: {contracts} | 当前价: ${current_price:.2f}")

        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side=side,
            amount=contracts,
            params={'tdMode': 'isolated', 'posSide': pos_side}
        )
        fill_price = order.get('average') or order.get('price') or current_price
        log.info(f"✅ [{cst_str()}] 开仓成功 | 成交均价: ${fill_price:.2f} | 订单ID: {order.get('id')}")

        stop_loss_price, take_profit_price, _ = calculate_stop_loss_take_profit(fill_price, direction)
        
        log.info(f"📉 止损价: ${stop_loss_price:.4f} | 📈 止盈价: ${take_profit_price:.4f}")
        if TRAILING_STOP_ENABLE:
            log.info(f"🚀 移动止损已启用 | 启动盈利: {TRAILING_STOP_START_RATIO*100:.1f}% | 追踪比例: {TRAILING_STOP_RATIO*100:.1f}%")

        position_tracker['entry_price']               = float(fill_price)
        position_tracker['entry_time']                = now_utc()
        position_tracker['position']                  = direction
        position_tracker['open_contracts']            = contracts
        position_tracker['entry_entry_macd_direction'] = entry_5m_direction
        position_tracker['stop_loss_price']           = stop_loss_price
        position_tracker['take_profit_price']         = take_profit_price
        position_tracker['trailing_stop_price']       = None
        position_tracker['max_profit_price']          = fill_price if direction == 'long' else None
        position_tracker['min_profit_price']          = fill_price if direction == 'short' else None
        save_state()
        return True

    except Exception as e:
        log.error(f"❌ 开仓失败: {e}")
        return False

def get_actual_position():
    try:
        positions = exchange.fetch_positions([SYMBOL])
        for pos in positions:
            contracts_float = float(pos.get('contracts', 0))
            pos_side = pos.get('side', pos.get('posSide', 'unknown'))
            info = pos.get('info', {})

            if abs(contracts_float) > 0.00000001:
                contracts = abs(contracts_float)
                info_pos_side = info.get('posSide', '').lower()
                if info_pos_side == 'short' or pos_side == 'short':
                    direction = 'short'
                else:
                    direction = 'long'
                return direction, contracts

        all_positions = exchange.fetch_positions()
        for pos in all_positions:
            symbol = pos.get('symbol', 'unknown')
            contracts_float = float(pos.get('contracts', 0))
            if symbol == SYMBOL and abs(contracts_float) > 0.00000001:
                contracts = abs(contracts_float)
                direction = 'long' if contracts_float > 0 else 'short'
                return direction, contracts

        return None, None
    except Exception as e:
        log.warning(f"获取实际持仓失败: {e}")
        return None, None

def check_stop_loss_take_profit(current_price: float) -> tuple[bool, str]:
    position = position_tracker.get('position')
    if not position:
        return False, '无持仓'
    
    entry_price = position_tracker.get('entry_price')
    stop_loss_price = position_tracker.get('stop_loss_price')
    take_profit_price = position_tracker.get('take_profit_price')
    
    if entry_price is None:
        return False, '未记录开仓价'
    
    if position == 'long':
        if stop_loss_price is not None and current_price <= stop_loss_price:
            return True, f'止损触发 | 当前价: ${current_price:.4f} <= 止损价: ${stop_loss_price:.4f}'
        
        if take_profit_price is not None and current_price >= take_profit_price:
            return True, f'止盈触发 | 当前价: ${current_price:.4f} >= 止盈价: ${take_profit_price:.4f}'
    else:
        if stop_loss_price is not None and current_price >= stop_loss_price:
            return True, f'止损触发 | 当前价: ${current_price:.4f} >= 止损价: ${stop_loss_price:.4f}'
        
        if take_profit_price is not None and current_price <= take_profit_price:
            return True, f'止盈触发 | 当前价: ${current_price:.4f} <= 止盈价: ${take_profit_price:.4f}'
    
    return False, '未触发止盈止损'

def check_trailing_stop(current_price: float) -> tuple[bool, str]:
    if not TRAILING_STOP_ENABLE:
        return False, '移动止损未启用'
    
    position = position_tracker.get('position')
    if not position:
        return False, '无持仓'
    
    entry_price = position_tracker.get('entry_price')
    if entry_price is None:
        return False, '未记录开仓价'
    
    if position == 'long':
        max_profit_price = position_tracker.get('max_profit_price')
        
        if max_profit_price is None:
            position_tracker['max_profit_price'] = current_price
            save_state()
            return False, '初始化最高盈利价'
        
        if current_price > max_profit_price:
            position_tracker['max_profit_price'] = current_price
            new_trailing_stop = current_price * (1 - TRAILING_STOP_RATIO)
            
            if position_tracker['trailing_stop_price'] is None:
                trailing_start_price = entry_price * (1 + TRAILING_STOP_START_RATIO)
                if current_price >= trailing_start_price:
                    position_tracker['trailing_stop_price'] = new_trailing_stop
                    log.info(f"🚀 移动止损启动 | 当前价: ${current_price:.4f} | 止损价: ${new_trailing_stop:.4f}")
            else:
                if new_trailing_stop > position_tracker['trailing_stop_price']:
                    position_tracker['trailing_stop_price'] = new_trailing_stop
                    log.info(f"📈 移动止损上移 | 当前价: ${current_price:.4f} | 新止损价: ${new_trailing_stop:.4f}")
            
            save_state()
            return False, '更新最高盈利价'
        
        trailing_stop_price = position_tracker.get('trailing_stop_price')
        if trailing_stop_price is not None and current_price <= trailing_stop_price:
            return True, f'移动止损触发 | 当前价: ${current_price:.4f} <= 止损价: ${trailing_stop_price:.4f}'
    else:
        min_profit_price = position_tracker.get('min_profit_price')
        
        if min_profit_price is None:
            position_tracker['min_profit_price'] = current_price
            save_state()
            return False, '初始化最低盈利价'
        
        if current_price < min_profit_price:
            position_tracker['min_profit_price'] = current_price
            new_trailing_stop = current_price * (1 + TRAILING_STOP_RATIO)
            
            if position_tracker['trailing_stop_price'] is None:
                trailing_start_price = entry_price * (1 - TRAILING_STOP_START_RATIO)
                if current_price <= trailing_start_price:
                    position_tracker['trailing_stop_price'] = new_trailing_stop
                    log.info(f"🚀 移动止损启动 | 当前价: ${current_price:.4f} | 止损价: ${new_trailing_stop:.4f}")
            else:
                if new_trailing_stop < position_tracker['trailing_stop_price']:
                    position_tracker['trailing_stop_price'] = new_trailing_stop
                    log.info(f"📉 移动止损下移 | 当前价: ${current_price:.4f} | 新止损价: ${new_trailing_stop:.4f}")
            
            save_state()
            return False, '更新最低盈利价'
        
        trailing_stop_price = position_tracker.get('trailing_stop_price')
        if trailing_stop_price is not None and current_price >= trailing_stop_price:
            return True, f'移动止损触发 | 当前价: ${current_price:.4f} >= 止损价: ${trailing_stop_price:.4f}'
    
    return False, '未触发移动止损'

def close_position(reason: str = '') -> bool:
    try:
        actual_direction, actual_contracts = get_actual_position()

        if actual_direction is None:
            log.warning("获取交易所持仓失败，尝试使用本地记录")
            actual_direction = position_tracker.get('position')
            actual_contracts = position_tracker.get('open_contracts')
            
            if actual_direction is None or actual_contracts is None:
                log.warning("本地也没有持仓记录")
                position_tracker['entry_price']               = None
                position_tracker['entry_time']                = None
                position_tracker['position']                  = None
                position_tracker['entry_entry_macd_direction'] = None
                position_tracker['open_contracts']            = None
                position_tracker['stop_loss_price']           = None
                position_tracker['take_profit_price']         = None
                position_tracker['trailing_stop_price']       = None
                position_tracker['max_profit_price']          = None
                position_tracker['min_profit_price']          = None
                save_state()
                return True

        log.info(f"检测到持仓: {actual_direction} {actual_contracts}")
        log.info(f"⬇️  [{cst_str()}] 执行平仓 | 原因: {reason}")

        side = 'sell' if actual_direction == 'long' else 'buy'
        pos_side = 'long' if actual_direction == 'long' else 'short'

        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side=side,
            amount=actual_contracts,
            params={
                'tdMode': 'isolated',
                'posSide': pos_side,
                'reduceOnly': True
            }
        )

        order_id = order.get('id')
        fill_price = order.get('average') or order.get('price') or 0
        
        if fill_price == 0 and order_id:
            log.info(f"等待1秒后重新查询订单...")
            time.sleep(1)
            try:
                order_info = exchange.fetch_order(order_id, SYMBOL)
                fill_price = order_info.get('average') or order_info.get('price') or 0
            except Exception as e:
                log.warning(f"重新查询订单失败: {e}")
        
        if fill_price == 0:
            log.info(f"使用ticker当前价作为参考")
            try:
                ticker = exchange.fetch_ticker(SYMBOL)
                fill_price = float(ticker.get('last', 0))
            except Exception as e:
                log.warning(f"获取ticker失败: {e}")
        
        log.info(f"✅ [{cst_str()}] 平仓成功 | 成交均价: ${fill_price:.2f} | 订单ID: {order_id}")

        entry_price = position_tracker.get('entry_price')
        position = position_tracker.get('position')
        contracts = position_tracker.get('open_contracts')
        
        if entry_price is not None and contracts is not None:
            contract_size = get_contract_size()
            if position == 'long':
                profit = (fill_price - entry_price) * contracts * contract_size
            else:
                profit = (entry_price - fill_price) * contracts * contract_size
            
            profit_status = '盈利' if profit >= 0 else '亏损'
            log.info(f"💰 本次交易 {profit_status}: ${profit:.2f} | "
                     f"开仓价: ${entry_price:.2f} | 平仓价: ${fill_price:.2f}")
            
            position_tracker['last_trade_loss'] = (profit < 0)
            
            trade_record = {
                'entry_time': position_tracker.get('entry_time').isoformat() if position_tracker.get('entry_time') else None,
                'exit_time': now_utc().isoformat(),
                'direction': position,
                'entry_price': entry_price,
                'exit_price': fill_price,
                'contracts': contracts,
                'profit': profit,
                'profit_status': profit_status,
                'reason': reason
            }
            
            position_tracker['trade_history'].insert(0, trade_record)
            if len(position_tracker['trade_history']) > 50:
                position_tracker['trade_history'] = position_tracker['trade_history'][:50]

        position_tracker['entry_price']               = None
        position_tracker['entry_time']                = None
        position_tracker['position']                  = None
        position_tracker['entry_entry_macd_direction'] = None
        position_tracker['open_contracts']            = None
        position_tracker['stop_loss_price']           = None
        position_tracker['take_profit_price']         = None
        position_tracker['trailing_stop_price']       = None
        position_tracker['max_profit_price']          = None
        position_tracker['min_profit_price']          = None
        save_state()
        return True

    except Exception as e:
        log.error(f"❌ 平仓失败: {e}", exc_info=True)
        return False

# ==================== K线与指标 ====================
def fetch_klines(exch, symbol, timeframe, limit=100):
    ohlcv = exch.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    for col in ('open', 'high', 'low', 'close'):
        df[col] = pd.to_numeric(df[col])
    return df

def calculate_macd_okx(prices, fast, slow, signal):
    s = pd.Series(prices)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = (macd_line - signal_line) * 2
    return macd_line.values, signal_line.values, histogram.values

def calculate_rsi(prices, length=14):
    s = pd.Series(prices)
    delta = s.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.values

def calculate_indicators(df, timeframe, fast, slow, signal, rsi_len=9):
    min_required = max(slow + signal, rsi_len + 1, 60)
    if len(df) < min_required:
        log.warning(f"数据点不足 [{timeframe}]: 当前{len(df)}根, 需要至少{min_required}根")
        for col in ('macd', 'macd_signal', 'macd_hist', 'rsi', 'ma9', 'ma21', 'ma60', 'ema9', 'ema21', 'ema60'):
            df[col] = float('nan')
        return df

    macd_line, signal_line, histogram = calculate_macd_okx(df['close'].values, fast, slow, signal)
    df['macd'] = macd_line
    df['macd_signal'] = signal_line
    df['macd_hist'] = histogram
    df['rsi'] = calculate_rsi(df['close'].values, rsi_len)
    
    # 计算MA和EMA均线
    df['ma9'] = df['close'].rolling(window=9).mean()
    df['ma21'] = df['close'].rolling(window=21).mean()
    df['ma60'] = df['close'].rolling(window=60).mean()
    df['ema9'] = df['close'].ewm(span=9, adjust=True).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=True).mean()
    df['ema60'] = df['close'].ewm(span=60, adjust=True).mean()
    
    return df

# ==================== MACD趋势判断 ====================
def _macd_trend_from_hist(current_hist, prev_hist) -> str:
    if current_hist > prev_hist:
        return 'long'
    else:
        return 'short'

def get_macd_trend_okx(df, idx=-1):
    if len(df) < abs(idx) + 2:
        return None
    cur = df['macd_hist'].iloc[idx]
    prv = df['macd_hist'].iloc[idx - 1]
    if pd.isna(cur) or pd.isna(prv):
        if pd.notna(cur):
            return 'long' if cur > 0 else 'short'
        return None
    return _macd_trend_from_hist(cur, prv)

def get_macd_trend_trend(df, timeframe):
    if len(df) < 3:
        return None
    tf_minutes = parse_timeframe_minutes(timeframe)
    latest_close_time = df['timestamp'].iloc[-1] + timedelta(minutes=tf_minutes)
    time_until_close = (latest_close_time - now_utc()).total_seconds()
    if time_until_close < 300 or latest_close_time <= now_utc():
        idx = -1
    else:
        idx = -2
    if idx == -2 and len(df) < 4:
        return None
    cur = df['macd_hist'].iloc[idx]
    prv = df['macd_hist'].iloc[idx - 1]
    log.info(f"📊 {timeframe} MACD: 当前={cur:.6f}, 前值={prv:.6f}")
    if pd.isna(cur):
        return None
    return _macd_trend_from_hist(cur, prv)

def get_closed_trend(df, timeframe):
    if df is None or len(df) < 3:
        return None
    
    tf_minutes = parse_timeframe_minutes(timeframe)
    latest_ts = df['timestamp'].iloc[-1]
    latest_close_time = latest_ts + timedelta(minutes=tf_minutes)
    time_until_close = (latest_close_time - now_utc()).total_seconds()
    
    if now_utc() >= latest_close_time or time_until_close <= 3:
        idx = -1
        if now_utc() >= latest_close_time:
            log.info(f"📊 已收盘{timeframe}趋势: 使用当前柱（已收盘）")
        else:
            log.info(f"📊 已收盘{timeframe}趋势: 使用当前柱（即将收盘）")
    else:
        idx = -2
        log.info(f"📊 已收盘{timeframe}趋势: 使用前一根已收盘柱")
    
    if len(df) < abs(idx) + 2:
        return None
    
    cur = df['macd_hist'].iloc[idx]
    prv = df['macd_hist'].iloc[idx - 1]
    
    if pd.isna(cur):
        return None
    
    closed_trend = _macd_trend_from_hist(cur, prv)
    log.info(f"📊 已收盘{timeframe}: hist={cur:.6f}, 前值={prv:.6f} → 趋势={closed_trend.upper()}")
    return closed_trend

def get_entry_current_trend(df):
    if len(df) < 2:
        return None
    return get_macd_trend_okx(df, idx=-1)

def get_entry_prev_trend(df):
    if len(df) < 3:
        return None
    return get_macd_trend_okx(df, idx=-2)

# ==================== 核心策略逻辑 ====================
def check_entry_reversal(df_entry, df_trend=None) -> tuple[str | None, str | None]:
    current_trend = get_entry_current_trend(df_entry)
    prev_trend = position_tracker.get('last_entry_macd_direction')
    
    if prev_trend is None:
        prev_trend = get_entry_prev_trend(df_entry)

    if current_trend is None or prev_trend is None:
        log.warning(f"无法获取{ENTRY_TIMEFRAME} MACD趋势")
        return None, None

    log.info(f"🔍 {ENTRY_TIMEFRAME} MACD | 上一柱={prev_trend} → 当前柱={current_trend}")

    if current_trend != prev_trend:
        log.info(f"⚡ {ENTRY_TIMEFRAME} MACD趋势反转: {prev_trend} → {current_trend}")
        
        # 使用大趋势K线判断均线位置
        if df_trend is not None and len(df_trend) >= 1 and 'ma9' in df_trend.columns:
            current_price = df_trend['close'].iloc[-1]
            ma9 = df_trend['ma9'].iloc[-1]
            ma21 = df_trend['ma21'].iloc[-1]
            ma60 = df_trend['ma60'].iloc[-1]
            ema9 = df_trend['ema9'].iloc[-1]
            ema21 = df_trend['ema21'].iloc[-1]
            ema60 = df_trend['ema60'].iloc[-1]
            
            # 检查价格是否在所有均线之上/之下
            above_all_ma = current_price > ma9 and current_price > ma21 and current_price > ma60
            below_all_ma = current_price < ma9 and current_price < ma21 and current_price < ma60
            above_all_ema = current_price > ema9 and current_price > ema21 and current_price > ema60
            below_all_ema = current_price < ema9 and current_price < ema21 and current_price < ema60
            
            log.info(f"📊 {TREND_TIMEFRAME} 价格: {current_price:.2f} | MA9:{ma9:.2f} MA21:{ma21:.2f} MA60:{ma60:.2f}")
            log.info(f"📊 {TREND_TIMEFRAME} EMA9:{ema9:.2f} EMA21:{ema21:.2f} EMA60:{ema60:.2f}")
            
            price_in_middle = not (above_all_ma and above_all_ema) and not (below_all_ma and below_all_ema)
            
            if current_trend == 'long':
                # 开多时，如果价格在所有均线上方，不需要RSI过滤
                if above_all_ma and above_all_ema:
                    log.info(f"✅ {TREND_TIMEFRAME}价格在所有均线上方，不过滤开多信号")
                elif price_in_middle:
                    # 价格在均线中间，需要放量确认
                    volume_ok = check_trend_volume(df_trend)
                    if volume_ok:
                        log.info(f"✅ {TREND_TIMEFRAME}价格在均线中间，但放量确认，不过滤开多信号")
                    else:
                        log.info(f"❌ {TREND_TIMEFRAME}价格在均线中间，且未放量，过滤开多信号")
                        return None, current_trend
                else:
                    # 检查RSI
                    if len(df_entry) >= 2 and 'rsi' in df_entry.columns:
                        current_rsi = df_entry['rsi'].iloc[-1]
                        log.info(f"📊 当前{ENTRY_TIMEFRAME} RSI: {current_rsi:.2f}")
                        if current_rsi >= RSI_OVERBOUGHT:
                            log.info(f"❌ RSI({current_rsi:.2f}) >= {RSI_OVERBOUGHT}，过滤开多信号")
                            return None, current_trend
            else:
                # 开空时，如果价格在所有均线下方，不需要RSI过滤
                if below_all_ma and below_all_ema:
                    log.info(f"✅ {TREND_TIMEFRAME}价格在所有均线下方，不过滤开空信号")
                elif price_in_middle:
                    # 价格在均线中间，需要放量确认
                    volume_ok = check_trend_volume(df_trend)
                    if volume_ok:
                        log.info(f"✅ {TREND_TIMEFRAME}价格在均线中间，但放量确认，不过滤开空信号")
                    else:
                        log.info(f"❌ {TREND_TIMEFRAME}价格在均线中间，且未放量，过滤开空信号")
                        return None, current_trend
                else:
                    # 检查RSI
                    if len(df_entry) >= 2 and 'rsi' in df_entry.columns:
                        current_rsi = df_entry['rsi'].iloc[-1]
                        log.info(f"📊 当前{ENTRY_TIMEFRAME} RSI: {current_rsi:.2f}")
                        if current_rsi <= RSI_OVERSOLD:
                            log.info(f"❌ RSI({current_rsi:.2f}) <= {RSI_OVERSOLD}，过滤开空信号")
                            return None, current_trend
        
        return current_trend, current_trend
    else:
        log.info(f"➡️  {ENTRY_TIMEFRAME} MACD无反转，方向持续: {current_trend}")
        return None, current_trend

def check_trend_volume(df) -> bool:
    """检查趋势K线是否放量（当前成交量 >= 前4-5根平均的1.5-2倍）"""
    if df is None or len(df) < 6 or 'volume' not in df.columns:
        log.warning(f"❌ {TREND_TIMEFRAME}数据不足，无法检查放量")
        return False
    
    current_volume = df['volume'].iloc[-1]
    # 计算前4-5根K线的平均成交量
    avg_volume = df['volume'].iloc[-5:-1].mean()  # 取前4根
    
    if avg_volume == 0:
        log.warning("❌ 平均成交量为0，无法检查放量")
        return False
    
    volume_ratio = current_volume / avg_volume
    log.info(f"📊 {TREND_TIMEFRAME}放量检查 | 当前量={current_volume:.0f} | 前4根平均={avg_volume:.0f} | 倍数={volume_ratio:.2f}x")
    
    # 放量为前4-5根平均的1.5-2倍
    if volume_ratio >= 1.5 and volume_ratio <= 3.0:
        log.info(f"✅ 放量确认 ({volume_ratio:.2f}x)")
        return True
    else:
        log.info(f"❌ 未放量 ({volume_ratio:.2f}x)，需要1.5-3.0x")
        return False

def check_trend_exit(df_entry, df_trend) -> bool:
    if not position_tracker.get('position'):
        log.info("无持仓，跳过平仓检查")
        return False

    closed_trend = get_closed_trend(df_trend, TREND_TIMEFRAME)
    entry_dir = position_tracker.get('entry_entry_macd_direction')
    open_direction = position_tracker.get('position')

    if closed_trend is None:
        log.warning(f"无法获取已收盘{TREND_TIMEFRAME} MACD趋势，使用{ENTRY_TIMEFRAME}趋势作为兜底")
        current_entry_trend = get_entry_current_trend(df_entry)
        if current_entry_trend is None:
            log.warning(f"{ENTRY_TIMEFRAME}趋势也无法获取，跳过平仓检查")
            return False
        if open_direction == 'long' and current_entry_trend == 'short':
            log.info(f"📊 兜底平仓: 持仓long，但{ENTRY_TIMEFRAME}趋势变short")
            return True
        elif open_direction == 'short' and current_entry_trend == 'long':
            log.info(f"📊 兜底平仓: 持仓short，但{ENTRY_TIMEFRAME}趋势变long")
            return True
        else:
            return False

    if entry_dir is None:
        log.warning(f"开仓{ENTRY_TIMEFRAME}方向未记录，跳过平仓检查")
        return False

    log.info(f"📊 平仓检查 | 已收盘{TREND_TIMEFRAME}趋势={closed_trend} | 开仓时{ENTRY_TIMEFRAME}方向={entry_dir}")

    should_close = False
    if entry_dir == 'long' and closed_trend == 'short':
        should_close = True
        reason = f"开仓时{ENTRY_TIMEFRAME}=long，但已收盘{TREND_TIMEFRAME}=short"
    elif entry_dir == 'short' and closed_trend == 'long':
        should_close = True
        reason = f"开仓时{ENTRY_TIMEFRAME}=short，但已收盘{TREND_TIMEFRAME}=long"
    else:
        reason = f"已收盘{TREND_TIMEFRAME}趋势与开仓时{ENTRY_TIMEFRAME}方向一致，继续持仓"

    log.info(f"平仓判断: {reason}")

    if should_close:
        log.info(f"⚠️ 触发平仓")
        return True
    else:
        log.info(f"✅ 继续持仓")
        return False

# ==================== 策略检查入口 ====================
def run_strategy_check(is_trend_check=False, is_entry_check=False):
    now_str = cst_str()
    position_status = position_tracker.get('position')

    current_price = None
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        current_price = float(ticker['last'])
    except Exception as e:
        log.error(f"获取价格失败: {e}")

    df_trend = None
    df_entry = None
    try:
        df_trend = fetch_klines(exchange, SYMBOL, TREND_TIMEFRAME, 120)
        df_trend = calculate_indicators(df_trend, TREND_TIMEFRAME, MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH)
    except Exception as e:
        log.error(f"获取{TREND_TIMEFRAME} K线失败: {e}")

    try:
        df_entry = fetch_klines(exchange, SYMBOL, ENTRY_TIMEFRAME, 100)
        df_entry = calculate_indicators(df_entry, ENTRY_TIMEFRAME, MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH)
    except Exception as e:
        log.error(f"获取{ENTRY_TIMEFRAME} K线失败: {e}")

    macd_trend_trend = get_macd_trend_trend(df_trend, TREND_TIMEFRAME) if df_trend is not None else None
    macd_entry_trend = get_macd_trend_okx(df_entry) if df_entry is not None else None

    log.info(f"\n[{now_str}] 策略检查 | trend_check={is_trend_check} | entry_check={is_entry_check}")
    log.info(f"{TREND_TIMEFRAME}={macd_trend_trend} | {ENTRY_TIMEFRAME}={macd_entry_trend} | 价格={'$'+f'{current_price:.2f}' if current_price else '未获取'}")
    log.info(f"持仓={position_status or '无'} | last_entry={position_tracker['last_entry_macd_direction']}")

    if is_trend_check and df_entry is not None and df_trend is not None:
        log.info(f"\n[{get_trend_check_time()}] 检查平仓条件...")
        should_close = check_trend_exit(df_entry, df_trend)
        if should_close:
            if close_position(f'{TREND_TIMEFRAME}方向与开仓时{ENTRY_TIMEFRAME}方向不同'):
                position_status = None
                # 平仓后立即检查开仓条件
                log.info("\n[平仓后] 立即检查开仓条件...")
                open_signal, current_entry = check_entry_reversal(df_entry, df_trend)
                
                if current_entry is not None:
                    position_tracker['last_entry_macd_direction'] = current_entry
                    save_state()

                if open_signal:
                    # 确定最终开仓方向
                    final_open_signal = open_signal
                    
                    # 上次亏损时需要双重确认（先看大趋势，再看入场趋势）
                    double_confirm = False
                    if position_tracker.get('last_trade_loss'):
                        log.info(f"⚠️ 上次亏损，需双重确认")
                        closed_trend = get_closed_trend(df_trend, TREND_TIMEFRAME)
                        current_entry_trend = get_entry_current_trend(df_entry)
                        log.info(f"📊 双重确认 | {TREND_TIMEFRAME}趋势={closed_trend} | {ENTRY_TIMEFRAME}趋势={current_entry_trend}")
                        
                        # 双重确认逻辑：大趋势决定开仓方向，入场趋势需要确认这个方向
                        if closed_trend == current_entry_trend:
                            double_confirm = True
                            # 用大趋势作为开仓方向
                            final_open_signal = closed_trend
                            log.info(f"✅ 双重确认成功: 使用{TREND_TIMEFRAME}趋势 {final_open_signal.upper()} 作为开仓方向")
                        else:
                            log.info(f"❌ 双重确认失败: {TREND_TIMEFRAME}与{ENTRY_TIMEFRAME}趋势不一致")
                
                    # 开仓条件：上次盈利直接开仓，上次亏损需要双重确认
                    should_open = not position_tracker.get('last_trade_loss') or double_confirm
                    
                    if should_open:
                        current_price = None
                        try:
                            ticker = exchange.fetch_ticker(SYMBOL)
                            current_price = float(ticker['last'])
                        except Exception as e:
                            log.error(f"获取价格失败: {e}")
                            
                        confirm_type = "双重确认" if position_tracker.get('last_trade_loss') else "趋势确认"
                        log.info(f"📈 反转信号: {final_open_signal.upper()}，{confirm_type} → 开仓")
                        if current_price:
                            success = open_position(final_open_signal, current_price, final_open_signal)
                            if success:
                                log.info(f"✅ 开仓完成: {final_open_signal.upper()} @ ${current_price:.2f}")
                                position_status = final_open_signal
                            else:
                                log.error("❌ 开仓失败")
                        else:
                            log.error("❌ 无法获取价格，开仓取消")
                    else:
                        log.info(f"⚠️ 反转信号: {open_signal.upper()}，但双重确认失败 → 不开仓")

    if is_entry_check and df_entry is not None:
        log.info(f"\n[{get_entry_check_time()}] 检测{ENTRY_TIMEFRAME} MACD反转...")
        open_signal, current_entry = check_entry_reversal(df_entry, df_trend)
        
        if current_entry is not None:
            position_tracker['last_entry_macd_direction'] = current_entry
            save_state()

        if open_signal:
            if not position_status:
                # 确定最终开仓方向
                final_open_signal = open_signal
                
                # 上次亏损时需要双重确认（先看大趋势，再看入场趋势）
                double_confirm = False
                if position_tracker.get('last_trade_loss'):
                    log.info(f"⚠️ 上次亏损，需双重确认")
                    closed_trend = get_closed_trend(df_trend, TREND_TIMEFRAME) if df_trend else None
                    current_entry_trend = get_entry_current_trend(df_entry)
                    log.info(f"📊 双重确认 | {TREND_TIMEFRAME}趋势={closed_trend} | {ENTRY_TIMEFRAME}趋势={current_entry_trend}")
                    
                    # 双重确认逻辑：大趋势决定开仓方向，入场趋势需要确认这个方向
                    if closed_trend == current_entry_trend:
                        double_confirm = True
                        # 用大趋势作为开仓方向
                        final_open_signal = closed_trend
                        log.info(f"✅ 双重确认成功: 使用{TREND_TIMEFRAME}趋势 {final_open_signal.upper()} 作为开仓方向")
                    else:
                        log.info(f"❌ 双重确认失败: {TREND_TIMEFRAME}与{ENTRY_TIMEFRAME}趋势不一致")
                
                # 开仓条件：上次盈利直接开仓，上次亏损需要双重确认
                should_open = not position_tracker.get('last_trade_loss') or double_confirm
                
                if should_open:
                    confirm_type = "双重确认" if position_tracker.get('last_trade_loss') else "趋势确认"
                    log.info(f"📈 反转信号: {final_open_signal.upper()}，{confirm_type} → 开仓")
                    if current_price:
                        success = open_position(final_open_signal, current_price, final_open_signal)
                        if success:
                            log.info(f"✅ 开仓完成: {final_open_signal.upper()} @ ${current_price:.2f}")
                    else:
                        log.error("❌ 无法获取价格，开仓取消")
                else:
                    log.info(f"⚠️ 反转信号: {open_signal.upper()}，但双重确认失败 → 不开仓")
            else:
                log.info(f"⚠️ 反转信号: {open_signal.upper()}，但当前有持仓({position_status}) → 不开仓")
        else:
            log.info(f"➡️  无反转信号，持仓不变")

    log.info("本次检查完成\n")

# ==================== 主循环 ====================
def test_api_connection():
    """测试API连接是否正常"""
    log.info("🔍 测试API连接...")
    try:
        # 测试获取价格
        ticker = exchange.fetch_ticker(SYMBOL)
        price = ticker.get('last')
        if price is None:
            log.error("❌ API测试失败: 无法获取价格")
            return False
        log.info(f"✅ 价格获取成功: {SYMBOL} = {price} USDT")
        
        # 测试获取持仓
        positions = exchange.fetch_positions([SYMBOL])
        log.info(f"✅ 持仓获取成功: {len(positions)} 个持仓")
        
        # 测试获取账户信息
        balance = exchange.fetch_balance()
        usdt_balance = balance.get('USDT', {}).get('free', 0)
        log.info(f"✅ 账户信息获取成功: USDT余额 = {usdt_balance:.2f}")
        
        return True
    except Exception as e:
        log.error(f"❌ API测试失败: {e}")
        return False

def check_stop_loss_take_profit_loop():
    if not position_tracker.get('position'):
        return
    
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        current_price = float(ticker['last'])
    except Exception as e:
        log.error(f"获取价格失败: {e}")
        return
    
    stop_triggered, stop_reason = check_stop_loss_take_profit(current_price)
    if stop_triggered:
        log.info(f"⚠️ [{cst_str()}] {stop_reason}")
        close_position(stop_reason)
        return
    
    trailing_triggered, trailing_reason = check_trailing_stop(current_price)
    if trailing_triggered:
        log.info(f"⚠️ [{cst_str()}] {trailing_reason}")
        close_position(trailing_reason)

def get_trend_check_time():
    """获取大趋势检查时间描述"""
    tf_minutes = parse_timeframe_minutes(TREND_TIMEFRAME)
    if tf_minutes < 60:
        return f"{tf_minutes-1}:55"
    else:
        hours = tf_minutes // 60
        return f"{hours}小时K线收盘前5分钟"

def get_entry_check_time():
    """获取入场检查时间描述"""
    tf_minutes = parse_timeframe_minutes(ENTRY_TIMEFRAME)
    return f"{tf_minutes-1}:30"

def main():
    log.info("=== OKX 本地版交易策略机器人 已启动 ===")
    
    # 先测试API连接
    if not test_api_connection():
        log.error("❌ API连接失败，退出程序")
        exit(1)
    
    log.info(f"标的: {SYMBOL} | 目标保证金: {TARGET_MARGIN} USDT | 杠杆: {LEVERAGE}x")
    log.info(f"大趋势: {TREND_TIMEFRAME} MACD方向 | 入场: {ENTRY_TIMEFRAME} MACD反转")
    log.info(f"止盈止损: 止损={STOP_LOSS_RATIO*100:.1f}% | 止盈={TAKE_PROFIT_RATIO*100:.1f}%")
    if TRAILING_STOP_ENABLE:
        log.info(f"移动止损: 已启用 | 启动盈利={TRAILING_STOP_START_RATIO*100:.1f}% | 追踪比例={TRAILING_STOP_RATIO*100:.1f}%")
    log.info(f"状态已加载: position={position_tracker['position']} | last_entry={position_tracker['last_entry_macd_direction']}")

    entry_done_ts = None
    trend_done_ts = None
    sl_done_ts = None

    entry_minutes = parse_timeframe_minutes(ENTRY_TIMEFRAME)
    trend_minutes = parse_timeframe_minutes(TREND_TIMEFRAME)

    while True:
        now_utc = datetime.now(timezone.utc)
        now_cst = now_utc.astimezone(timezone(timedelta(hours=8)))
        m, s = now_cst.minute, now_cst.second

        # 入场检查：K线收盘后25-30秒（策略2优先，错开API请求）
        if not position_tracker.get('position') and m % entry_minutes == 0 and s >= 25 and s <= 30:
            if not entry_done_ts or now_utc > entry_done_ts + timedelta(minutes=entry_minutes - 1):
                log.info(f"[{now_cst.strftime('%H:%M:%S')}] 📊 检查{ENTRY_TIMEFRAME}开仓条件...")
                run_strategy_check(is_trend_check=False, is_entry_check=True)
                entry_done_ts = now_utc

        # 大趋势平仓检查：K线收盘后35-40秒（策略2优先，错开API请求）
        if position_tracker.get('position') and m % trend_minutes == 0 and s >= 35 and s <= 40:
            if not trend_done_ts or now_utc > trend_done_ts + timedelta(minutes=trend_minutes - 1):
                log.info(f"[{now_cst.strftime('%H:%M:%S')}] 📊 检查{TREND_TIMEFRAME}平仓条件...")
                run_strategy_check(is_trend_check=True, is_entry_check=False)
                trend_done_ts = now_utc

        # 止盈止损检查：每隔一段时间检查一次
        if position_tracker.get('position'):
            if not sl_done_ts or now_utc > sl_done_ts + timedelta(seconds=TRAILING_CHECK_INTERVAL):
                check_stop_loss_take_profit_loop()
                sl_done_ts = now_utc

        time.sleep(1)

if __name__ == "__main__":
    main()
