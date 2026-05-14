#!/usr/bin/env python3
"""
New Strategy Bot - 5min MACD反转开仓 + 30min MACD平仓

核心逻辑:
  [04:30] 检测5min MACD柱趋势是否反转 → 反转且无持仓 → 开仓
  [29:30] 判断30min MACD方向是否与开仓时5min方向不同 → 不同 → 平仓
  开仓后等待下一次5min反转才能再次开仓（自然防重入，无需开关）

变更说明（对比旧版）:
  - 移除: long_switch / short_switch 双开关体系
  - 移除: detect_historical_macd_changes（启动历史扫描）
  - 移除: TP/SL 止盈止损
  - 新增: last_5m_macd_direction 跟踪5min方向用于反转检测
  - 新增: entry_5m_macd_direction 记录开仓时的5min方向用于平仓比较
  - 入场: 5min MACD反转 → 开仓
  - 出场: 30min MACD ≠ 开仓5min方向 → 平仓
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

load_dotenv('binance_config.env')

# ==================== 日志 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ==================== 配置 ====================
SYMBOL          = 'ETH-USDT-SWAP'
TARGET_MARGIN   = 20        # 目标保证金（USDT）
LEVERAGE        = 7
RSI_LENGTH      = 9         # RSI周期

MACD_FAST       = 9
MACD_SLOW       = 21
MACD_SIGNAL     = 60

RSI_OVERBOUGHT  = 70    # RSI超买阈值（开多信号时过滤）
RSI_OVERSOLD    = 30    # RSI超卖阈值（开空信号时过滤）

STATE_FILE      = Path('state.json')

log.info("=== New Strategy Bot 已启动 (OKX) 5min反转策略 ===")
log.info(f"标的: {SYMBOL} | 目标保证金: {TARGET_MARGIN} USDT | 杠杆: {LEVERAGE}x | 合约价值: {TARGET_MARGIN * LEVERAGE} USDT | 逐仓")
log.info(f"入场: 5min MACD反转 @ 05:00（K线收盘后） | 出场: 30min MACD方向不同 @ 29:30")
log.info(f"RSI过滤: 开多要求RSI<{RSI_OVERBOUGHT} | 开空要求RSI>{RSI_OVERSOLD}")

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
    'entry_price':            None,   # 开仓均价
    'entry_time':             None,   # 开仓时间 (UTC)
    'position':               None,   # 当前方向: None | 'long' | 'short'
    'entry_5m_macd_direction': None,  # 开仓时记录的5min MACD方向，用于29:30平仓比较
    'last_5m_macd_direction':  None,  # 上一次04:30检查时的5min方向，用于检测反转
    'open_contracts':          None,  # 开仓张数，用于平仓
    'trade_history':           [],    # 交易历史记录（每笔平仓后记录）
    'last_trade_loss':         False, # 上一次平仓是否为亏损（用于增加开仓确认条件）
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
            return _deserialize(data)
        except Exception as e:
            log.warning(f"加载状态失败，使用默认值: {e}")
    return dict(_DEFAULT_TRACKER)

position_tracker = load_state()
log.info(
    f"状态已加载: position={position_tracker['position']} | "
    f"last_5m={position_tracker['last_5m_macd_direction']} | "
    f"entry_5m={position_tracker['entry_5m_macd_direction']}"
)

# 最后执行数据（内存，面板展示用）
last_execution = {
    'timestamp':              None,
    'current_price':          None,
    'macd_30m_trend':         None,
    'macd_5m_trend':          None,
    'last_5m_macd_direction': None,
    'reversal_detected':      False,
    'close_triggered':        False,
    'position':               None,
    'entry_price':            None,
    'entry_time':             None,
    'entry_5m_macd_direction': None,
}

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
    """获取合约面值"""
    if 'size' in _contract_size_cache:
        return _contract_size_cache['size']
    try:
        log.info(f"正在加载市场信息，获取合约面值...")
        markets = exchange.load_markets()
        key = SYMBOL.replace('-', '/').replace('USDT', 'USDT:USDT', 1)
        market = markets.get(key) or markets.get(SYMBOL)
        
        if market:
            log.info(f"找到市场信息: {key}")
            size = float(market['contractSize'])
            log.info(f"市场返回的合约面值: {size}")
        else:
            log.warning(f"未找到市场信息 {key} 或 {SYMBOL}")
            # 根据交易对设置默认合约面值
            if 'BTC' in SYMBOL:
                size = 0.01  # BTC-USDT-SWAP 默认合约面值是 0.01 BTC/张
            elif 'SOL' in SYMBOL:
                size = 1.0   # SOL-USDT-SWAP 默认合约面值是 1 SOL/张
            elif 'ETH' in SYMBOL:
                size = 0.1   # ETH-USDT-SWAP 默认合约面值是 0.1 ETH/张
            else:
                size = 0.01  # 默认使用BTC合约面值
    except Exception as e:
        log.error(f"获取合约面值失败: {e}", exc_info=True)
        # 根据交易对设置默认合约面值
        if 'BTC' in SYMBOL:
            size = 0.01
        elif 'SOL' in SYMBOL:
            size = 1.0
        elif 'ETH' in SYMBOL:
            size = 0.1
        else:
            size = 0.01
    _contract_size_cache['size'] = size
    
    # 提取标的币种显示
    base_coin = SYMBOL.split('-')[0] if '-' in SYMBOL else 'BTC'
    log.info(f"最终使用的合约面值: {size} {base_coin}/张")
    return size

def calculate_contracts(current_price: float) -> float:
    """根据目标保证金计算下单张数"""
    contract_size = get_contract_size()
    position_value = TARGET_MARGIN * LEVERAGE  # 合约价值 = 保证金 × 杠杆
    per_contract_value = current_price * contract_size  # 每张合约价值
    contracts = position_value / per_contract_value
    contracts = round(contracts, 4)  # 保留4位小数
    
    # 提取标的币种显示
    base_coin = SYMBOL.split('-')[0] if '-' in SYMBOL else 'BTC'
    
    log.info(f"开仓计算详情:")
    log.info(f"  目标保证金: {TARGET_MARGIN} USDT")
    log.info(f"  杠杆倍数: {LEVERAGE}x")
    log.info(f"  合约价值: {position_value} USDT")
    log.info(f"  合约面值: {contract_size} {base_coin}/张")
    log.info(f"  当前价格: ${current_price:.2f} USDT")
    log.info(f"  每张合约价值: {per_contract_value:.2f} USDT")
    log.info(f"  最终下单张数: {contracts:.4f}")
    
    return contracts

# ==================== 下单执行 ====================
def set_leverage_once():
    try:
        exchange.set_leverage(LEVERAGE, SYMBOL, params={'mgnMode': 'isolated'})
        log.info(f"杠杆已设置: {LEVERAGE}x 逐仓")
    except Exception as e:
        log.warning(f"设置杠杆失败（可能已设置）: {e}")

def open_position(direction: str, current_price: float, entry_5m_direction: str) -> bool:
    """
    开仓
    direction        : 'long' | 'short'
    entry_5m_direction: 开仓时的5min MACD方向（用于29:30平仓判断）
    返回 True 表示成功
    """
    try:
        set_leverage_once()
        contracts = calculate_contracts(current_price)
        side      = 'buy' if direction == 'long' else 'sell'

        log.info(f"⬆️  [{cst_str()}] 开仓 {direction.upper()} | 张数: {contracts} | 当前价: ${current_price:.2f}")

        pos_side = 'long' if direction == 'long' else 'short'
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side=side,
            amount=contracts,
            params={'tdMode': 'isolated', 'posSide': pos_side}
        )
        fill_price = order.get('average') or order.get('price') or current_price
        log.info(f"✅ [{cst_str()}] 开仓成功 | 成交均价: ${fill_price:.2f} | 订单ID: {order.get('id')}")

        position_tracker['entry_price']             = float(fill_price)
        position_tracker['entry_time']              = now_utc()
        position_tracker['position']                = direction
        position_tracker['open_contracts']          = contracts
        position_tracker['entry_5m_macd_direction'] = entry_5m_direction
        save_state()
        return True

    except Exception as e:
        log.error(f"❌ 开仓失败: {e}")
        return False

def get_actual_position():
    """
    从交易所获取实际持仓信息
    返回 (direction: str|None, contracts: float|None)
    """
    try:
        log.info(f"尝试获取持仓: {SYMBOL}")

        positions = exchange.fetch_positions([SYMBOL])
        log.info(f"fetch_positions 返回 {len(positions)} 个持仓")

        for pos in positions:
            contracts_float = float(pos.get('contracts', 0))
            pos_side = pos.get('side', pos.get('posSide', 'unknown'))
            info = pos.get('info', {})

            log.info(f"持仓详情:")
            log.info(f"  contracts: {contracts_float}")
            log.info(f"  side/posSide: {pos_side}")
            log.info(f"  info: {info}")

            if abs(contracts_float) > 0.00000001:
                contracts = abs(contracts_float)
                info_pos_side = info.get('posSide', '').lower()
                if info_pos_side == 'short' or pos_side == 'short':
                    direction = 'short'
                else:
                    direction = 'long'
                return direction, contracts

        log.info("未找到有效持仓，尝试获取所有持仓...")
        all_positions = exchange.fetch_positions()
        log.info(f"所有持仓共 {len(all_positions)} 个")

        for pos in all_positions:
            symbol = pos.get('symbol', 'unknown')
            contracts_float = float(pos.get('contracts', 0))

            if symbol == SYMBOL and abs(contracts_float) > 0.00000001:
                contracts = abs(contracts_float)
                direction = 'long' if contracts_float > 0 else 'short'
                log.info(f"从所有持仓中找到: {direction} {contracts}")
                return direction, contracts

        return None, None
    except Exception as e:
        log.warning(f"获取实际持仓失败: {e}")
        return None, None

def close_position(reason: str = '') -> bool:
    """
    平仓当前持仓（使用 create_order 直接下单）
    """
    try:
        actual_direction, actual_contracts = get_actual_position()

        # 优先使用交易所获取的持仓信息，如果获取失败则使用本地记录
        if actual_direction is None:
            log.warning("获取交易所持仓失败，尝试使用本地记录")
            # 使用本地记录
            actual_direction = position_tracker.get('position')
            actual_contracts = position_tracker.get('open_contracts')
            
            if actual_direction is None or actual_contracts is None:
                log.warning("本地也没有持仓记录")
                position_tracker['entry_price']             = None
                position_tracker['entry_time']              = None
                position_tracker['position']                = None
                position_tracker['entry_5m_macd_direction'] = None
                position_tracker['open_contracts']          = None
                save_state()
                return True
            else:
                log.info(f"使用本地记录: {actual_direction} {actual_contracts}")

        log.info(f"检测到持仓: {actual_direction} {actual_contracts}")
        log.info(f"⬇️  [{cst_str()}] 执行平仓 | 原因: {reason}")

        side     = 'sell' if actual_direction == 'long' else 'buy'
        pos_side = 'long' if actual_direction == 'long' else 'short'

        log.info(f"下单参数:")
        log.info(f"  symbol: {SYMBOL}")
        log.info(f"  side: {side}")
        log.info(f"  amount: {actual_contracts}")
        log.info(f"  posSide: {pos_side}")

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
        # 获取成交价：优先从订单返回值获取，若为0则等1秒重新查询订单，仍为0则用ticker当前价
        fill_price = order.get('average') or order.get('price') or 0
        
        if fill_price == 0 and order_id:
            log.info(f"⚠️ 订单返回成交价为0，等待1秒后重新查询...")
            time.sleep(1)
            try:
                order_info = exchange.fetch_order(order_id, SYMBOL)
                fill_price = order_info.get('average') or order_info.get('price') or 0
            except Exception as e:
                log.warning(f"重新查询订单失败: {e}")
        
        if fill_price == 0:
            log.info(f"⚠️ 仍无法获取成交价，使用ticker当前价作为参考")
            try:
                ticker = exchange.fetch_ticker(SYMBOL)
                fill_price = float(ticker.get('last', 0))
            except Exception as e:
                log.warning(f"获取ticker失败: {e}")
        
        log.info(f"✅ [{cst_str()}] 平仓成功 | 成交均价: ${fill_price:.2f} | 订单ID: {order_id}")

        # 计算盈亏并记录交易历史
        entry_price = position_tracker.get('entry_price')
        entry_time = position_tracker.get('entry_time')
        position = position_tracker.get('position')
        contracts = position_tracker.get('open_contracts')
        
        if entry_price is not None and contracts is not None:
            contract_size = get_contract_size()
            # 计算盈亏
            if position == 'long':
                profit = (fill_price - entry_price) * contracts * contract_size
            else:  # short
                profit = (entry_price - fill_price) * contracts * contract_size
            
            profit_status = '盈利' if profit >= 0 else '亏损'
            log.info(f"💰 本次交易 {profit_status}: ${profit:.2f} | "
                     f"开仓价: ${entry_price:.2f} | 平仓价: ${fill_price:.2f} | 张数: {contracts}")
            
            # 更新上次是否亏损的状态
            position_tracker['last_trade_loss'] = (profit < 0)
            log.info(f"📊 上次交易状态: {'亏损 → 下次开仓需双重确认' if profit < 0 else '盈利 → 正常开仓'}")
            
            # 记录交易历史
            trade_record = {
                'entry_time': entry_time.isoformat() if entry_time else None,
                'exit_time': now_utc().isoformat(),
                'direction': position,
                'entry_price': entry_price,
                'exit_price': fill_price,
                'contracts': contracts,
                'profit': profit,
                'profit_status': profit_status,
                'reason': reason
            }
            
            # 保持最近50条历史记录
            position_tracker['trade_history'].insert(0, trade_record)
            if len(position_tracker['trade_history']) > 50:
                position_tracker['trade_history'] = position_tracker['trade_history'][:50]

        position_tracker['entry_price']             = None
        position_tracker['entry_time']              = None
        position_tracker['position']                = None
        position_tracker['entry_5m_macd_direction'] = None
        position_tracker['open_contracts']          = None
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
    """匹配OKX实盘的MACD计算（adjust=False 标准递归EMA）"""
    s           = pd.Series(prices)
    ema_fast    = s.ewm(span=fast, adjust=False).mean()
    ema_slow    = s.ewm(span=slow, adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram   = (macd_line - signal_line) * 2  # OKX的MACD柱 = (DIF - DEA) × 2
    return macd_line.values, signal_line.values, histogram.values

def calculate_rsi(prices, length=14):
    """计算RSI指标"""
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
    min_required = max(slow + signal, rsi_len + 1)
    if len(df) < min_required:
        log.warning(f"数据点不足 [{timeframe}]: 当前{len(df)}根, 需要至少{min_required}根")
        for col in ('macd', 'macd_signal', 'macd_hist', 'rsi'):
            df[col] = float('nan')
        return df

    macd_line, signal_line, histogram = calculate_macd_okx(
        df['close'].values, fast, slow, signal
    )
    df['macd']        = macd_line
    df['macd_signal'] = signal_line
    df['macd_hist']   = histogram    # OKX: MACD柱状图 = MACD Line - Signal Line
    df['rsi']         = calculate_rsi(df['close'].values, rsi_len)
    return df

# ==================== MACD趋势判断 ====================
def _macd_trend_from_hist(current_hist, prev_hist) -> str:
    """
    OKX MACD趋势判断（基于柱子高度变化）:
      当前柱比前一根更高 → 动能增强 → LONG（深绿）
      当前柱比前一根更矮 → 动能减弱 → SHORT（浅绿）
    """
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

def get_macd_trend_30m(df30m):
    """
    判断30分钟MACD趋势（用于平仓判断）
    若最新K线未收盘但即将收盘（剩余<5分钟），直接使用当前K线
    否则取倒数第二根（已收盘）
    使用柱高变化判断趋势（与verify_trend_logic.py一致）：
      current_hist > prev_hist → long
      current_hist < prev_hist → short
    """
    if len(df30m) < 3:
        return None
    latest_close_time = df30m['timestamp'].iloc[-1] + timedelta(minutes=30)
    time_until_close = (latest_close_time - now_utc()).total_seconds()
    # 如果即将收盘（剩余<5分钟）或已收盘，使用当前K线
    if time_until_close < 300 or latest_close_time <= now_utc():
        idx = -1
    else:
        idx = -2
    if idx == -2 and len(df30m) < 4:
        return None
    cur = df30m['macd_hist'].iloc[idx]
    prv = df30m['macd_hist'].iloc[idx - 1]
    log.info(f"📊 30min MACD: 当前={cur:.6f}, 前值={prv:.6f}")
    if pd.isna(cur):
        return None
    # 使用柱高变化判断趋势（与verify_trend_logic.py一致）
    return _macd_trend_from_hist(cur, prv)

def get_closed_30m_trend(df30m):
    """
    获取已收盘的30分钟MACD趋势（用于上次亏损时的双重确认）
    - 必须使用已完全收盘的K线
    - 如果当前30min K线即将收盘（剩余<5分钟），也视为"即将走完"
    - 否则使用前一根已收盘的K线
    """
    if df30m is None or len(df30m) < 3:
        return None
    
    latest_ts = df30m['timestamp'].iloc[-1]
    latest_close_time = latest_ts + timedelta(minutes=30)
    time_until_close = (latest_close_time - now_utc()).total_seconds()
    
    # 判断使用哪根K线
    if time_until_close > 0 and time_until_close <= 300:  # 即将走完（<5分钟）
        idx = -1
        log.info(f"📊 已收盘30min趋势: 使用当前柱（即将走完，剩余{time_until_close:.0f}秒）")
    else:  # 未到收盘时间，使用前一根已收盘的
        idx = -2
        log.info(f"📊 已收盘30min趋势: 使用前一根已收盘柱")
    
    if len(df30m) < abs(idx) + 2:
        return None
    
    cur = df30m['macd_hist'].iloc[idx]
    prv = df30m['macd_hist'].iloc[idx - 1]
    
    if pd.isna(cur):
        return None
    
    closed_trend = _macd_trend_from_hist(cur, prv)
    log.info(f"📊 已收盘30min: hist={cur:.6f}, 前值={prv:.6f} → 趋势={closed_trend.upper()}")
    return closed_trend

def get_macd_trend_5m(df5m):
    """
    判断5分钟MACD趋势
    若最新K线未收盘，取倒数第二根（已收盘）
    """
    if len(df5m) < 3:
        return None
    latest_close_time = df5m['timestamp'].iloc[-1] + timedelta(minutes=5)
    idx = -1 if latest_close_time <= now_utc() else -2
    if idx == -2 and len(df5m) < 4:
        return None
    return get_macd_trend_okx(df5m, idx)

# ==================== 核心策略逻辑 ====================

def get_5m_current_trend(df5m):
    """
    获取当前柱（-1）的MACD趋势
    不管K线是否收盘，直接取最新
    """
    if len(df5m) < 2:
        return None
    return get_macd_trend_okx(df5m, idx=-1)

def get_5m_prev_trend(df5m):
    """
    获取上一根已收盘柱（-2）的MACD趋势
    """
    if len(df5m) < 3:
        return None
    return get_macd_trend_okx(df5m, idx=-2)

def check_5m_reversal(df5m) -> tuple[str | None, str | None]:
    """
    [05:00 调用] 检测5min MACD是否出现趋势反转（含RSI过滤）
    比较当前柱与上一次记录的5min方向
    返回: (open_signal, current_trend)
      open_signal: 'long' | 'short' | None
      current_trend: 当前5min趋势方向
    """
    current_trend = get_5m_current_trend(df5m)
    prev_trend = position_tracker.get('last_5m_macd_direction')
    
    # 如果没有上一次记录，使用上一柱的趋势作为初始值
    if prev_trend is None:
        prev_trend = get_5m_prev_trend(df5m)

    if current_trend is None or prev_trend is None:
        log.warning("⚠️ 无法获取5min MACD趋势，当前或上一根K线数据不足")
        return None, None

    log.info(f"🔍 5min MACD | 上一柱={prev_trend} → 当前柱={current_trend}")

    if current_trend != prev_trend:
        log.info(f"⚡ 5min MACD趋势反转: {prev_trend} → {current_trend}")
        
        # RSI过滤检查
        if len(df5m) >= 2 and 'rsi' in df5m.columns:
            current_rsi = df5m['rsi'].iloc[-1]
            log.info(f"📊 当前5min RSI: {current_rsi:.2f}")
            
            if current_trend == 'long':
                if current_rsi >= RSI_OVERBOUGHT:
                    log.info(f"❌ RSI({current_rsi:.2f}) >= {RSI_OVERBOUGHT}，过滤开多信号")
                    return None, current_trend
            else:  # short
                if current_rsi <= RSI_OVERSOLD:
                    log.info(f"❌ RSI({current_rsi:.2f}) <= {RSI_OVERSOLD}，过滤开空信号")
                    return None, current_trend
        else:
            log.warning("⚠️ RSI数据不足，跳过RSI过滤")
            
        return current_trend, current_trend
    else:
        log.info(f"➡️  5min MACD无反转，方向持续: {current_trend}")
        return None, current_trend


def check_30m_exit(df5m, df30m) -> bool:
    """
    [29:55 调用] 判断当前30min MACD趋势是否与开仓时5min方向不同
    设计逻辑：30min MACD方向与开仓时5min方向不同 → 平仓，返回 True
    兜底：若30min数据获取失败，改用5min当前趋势与持仓方向对比（反向则平仓）
    """
    if not position_tracker.get('position'):
        log.info("无持仓，跳过平仓检查")
        return False

    # 使用30min趋势判断平仓（而不是5min趋势）
    current_30m_trend = get_macd_trend_30m(df30m)
    entry_5m_dir     = position_tracker.get('entry_5m_macd_direction')
    open_direction   = position_tracker.get('position')

    # 兜底：30min数据获取失败时，使用5min趋势与持仓方向对比
    if current_30m_trend is None:
        log.warning("⚠️ 无法获取当前30min MACD趋势，使用5min趋势作为兜底")
        current_5m_trend = get_5m_current_trend(df5m)
        if current_5m_trend is None:
            log.warning("⚠️ 5min趋势也无法获取，跳过平仓检查")
            return False
        # 兜底逻辑：5min趋势与持仓方向反向则平仓
        if open_direction == 'long' and current_5m_trend == 'short':
            log.info(f"📊 兜底平仓: 持仓long，但5min趋势变short")
            return True
        elif open_direction == 'short' and current_5m_trend == 'long':
            log.info(f"📊 兜底平仓: 持仓short，但5min趋势变long")
            return True
        else:
            log.info(f"📊 兜底检查: 持仓{open_direction}，5min趋势{current_5m_trend}，继续持仓")
            return False

    if entry_5m_dir is None:
        log.warning("⚠️ 开仓5min方向未记录，跳过平仓检查")
        return False

    log.info(
        f"📊 29:55平仓检查 | 当前30min趋势={current_30m_trend} | "
        f"开仓时5min方向={entry_5m_dir} | 持仓方向={open_direction}"
    )

    if len(df30m) >= 2:
        cur_hist = df30m['macd_hist'].iloc[-1] if len(df30m) >= 1 else 0
        log.info(f"    30min MACD Hist: {cur_hist:.6f}")
    
    if len(df5m) >= 2:
        cur_hist = df5m['macd_hist'].iloc[-1]
        prv_hist = df5m['macd_hist'].iloc[-2]
        log.info(f"    当前5min MACD Hist: {cur_hist:.6f}, 前值: {prv_hist:.6f}")
        log.info(f"    当前RSI: {df5m['rsi'].iloc[-1]:.2f}")

    should_close = False
    # 关键修复：比较30min趋势与开仓时5min方向
    if entry_5m_dir == 'long' and current_30m_trend == 'short':
        should_close = True
        reason = "开仓时5min=long，但当前30min=short"
    elif entry_5m_dir == 'short' and current_30m_trend == 'long':
        should_close = True
        reason = "开仓时5min=short，但当前30min=long"
    else:
        reason = f"30min趋势与开仓时5min方向一致，继续持仓"

    log.info(f"    平仓判断: {reason}")

    if should_close:
        log.info(f"⚠️ 触发平仓")
        return True
    else:
        log.info(f"✅ 继续持仓")
        return False

# ==================== 主策略入口 ====================
def run_new_strategy_check(
    _,  # 忽略旧的 position_status 参数
    exch,
    symbol,
    fast, slow, signal, rsi_len,
    is_29_30_check=False,
    is_04_30_check=False
):
    global position_tracker, last_execution
    now_str          = cst_str()
    reversal_detected = False
    close_triggered   = False
    position_status  = position_tracker.get('position')  # 直接从 position_tracker 读取

    # --- 获取当前价格 ---
    current_price = None
    try:
        ticker        = exch.fetch_ticker(symbol)
        current_price = float(ticker['last'])
    except Exception as e:
        log.error(f"获取价格失败: {e}")

    # --- 拉取K线并计算指标 ---
    df30m = None
    df5m  = None
    try:
        df30m = fetch_klines(exch, symbol, '30m', 120)
        df30m = calculate_indicators(df30m, '30m', fast, slow, signal, rsi_len)
    except Exception as e:
        log.error(f"获取30m K线失败: {e}")

    try:
        df5m = fetch_klines(exch, symbol, '5m', 100)
        df5m = calculate_indicators(df5m, '5m', fast, slow, signal, rsi_len)
    except Exception as e:
        log.error(f"获取5m K线失败: {e}")

    macd_30m_trend = get_macd_trend_30m(df30m) if df30m is not None else None
    macd_5m_trend  = get_macd_trend_5m(df5m)  if df5m  is not None else None

    log.info(f"\n[{now_str}] 策略检查 | 29:30={is_29_30_check} | 04:30={is_04_30_check}")
    log.info(
        f"30min={macd_30m_trend} | 5min={macd_5m_trend} | "
        f"价格={'$'+f'{current_price:.2f}' if current_price else '未获取'}"
    )
    log.info(
        f"持仓={position_status or '无'} | "
        f"last_5m={position_tracker['last_5m_macd_direction']} | "
        f"entry_5m={position_tracker['entry_5m_macd_direction']}"
    )

    # ----------------------------------------------------------------
    # [29:55] 判断当前30min MACD是否与开仓时5min方向不同 → 平仓
    # ----------------------------------------------------------------
    if is_29_30_check and df5m is not None and df30m is not None:
        log.info("\n[29:55] 检查平仓条件...")
        should_close = check_30m_exit(df5m, df30m)
        if should_close:
            if close_position('当前30min方向与开仓时5min方向不同'):
                close_triggered = True
        # 更新 position_status
        position_status = position_tracker.get('position')

    # ----------------------------------------------------------------
    # [04:30] 检测5min MACD反转 → 无持仓则开仓
    # ----------------------------------------------------------------
    if is_04_30_check and df5m is not None:
        log.info("\n[04:30] 检测5min MACD反转...")
        open_signal, current_5m = check_5m_reversal(df5m)
        macd_5m_trend = current_5m  # 刷新展示值
        
        # 更新 last_5m_macd_direction
        if current_5m is not None:
            position_tracker['last_5m_macd_direction'] = current_5m
            save_state()

        if open_signal:
            reversal_detected = True
            if not position_status:
                # 检查30分钟趋势是否与开仓方向一致
                trend_confirm = False
                if macd_30m_trend == open_signal:
                    trend_confirm = True
                    log.info(f"✅ 30min趋势确认: {macd_30m_trend.upper()} 与开仓方向一致")
                else:
                    log.info(f"❌ 30min趋势: {macd_30m_trend.upper()} 与开仓方向不一致")
                
                # 上次亏损时需要双重确认：30min当前柱和已收盘柱都要与信号方向一致
                double_confirm = False
                if position_tracker.get('last_trade_loss'):
                    log.info(f"⚠️ 上次亏损，需双重确认（30min当前柱 + 已收盘柱都要同向）")
                    # 获取30min当前柱趋势
                    current_30m_trend = get_macd_trend_30m(df30m)
                    # 获取已收盘30min柱趋势
                    closed_30m_trend = get_closed_30m_trend(df30m)
                    # 两个条件都要满足
                    if current_30m_trend == open_signal and closed_30m_trend == open_signal:
                        double_confirm = True
                        log.info(f"✅ 双重确认成功: 30min当前={current_30m_trend.upper()}, 30min已收盘={closed_30m_trend.upper()}")
                    else:
                        log.info(f"❌ 双重确认失败: 30min当前={current_30m_trend.upper() if current_30m_trend else 'None'}, 30min已收盘={closed_30m_trend.upper() if closed_30m_trend else 'None'}")
                
                # 决定是否开仓
                should_open = trend_confirm and (not position_tracker.get('last_trade_loss') or double_confirm)
                
                if should_open:
                    # 无持仓 + 趋势确认 → 开仓
                    confirm_type = "双重确认" if position_tracker.get('last_trade_loss') else "趋势确认"
                    log.info(f"📈 反转信号: {open_signal.upper()}，当前无持仓，{confirm_type} → 开仓")
                    if current_price:
                        success = open_position(open_signal, current_price, open_signal)
                        if success:
                            position_status = open_signal
                            log.info(f"✅ 开仓完成: {open_signal.upper()} @ ${current_price:.2f}")
                    else:
                        log.error("❌ 无法获取价格，开仓取消")
                else:
                    # 趋势不确认或双重确认失败，不开仓
                    reason = "30min趋势不确认" if not trend_confirm else "双重确认失败"
                    log.info(f"⚠️ 反转信号: {open_signal.upper()}，但{reason} → 不开仓")
            else:
                # 有持仓，记录反转但不操作（等29:30平仓后再进入）
                log.info(
                    f"⚠️ 反转信号: {open_signal.upper()}，但当前有持仓({position_status}) → 不开仓"
                )
        else:
            log.info("➡️  无反转信号，持仓不变")
        
        # ----------------------------------------------------------------
        # [04:30] 新增：无持仓时，30min与5min同向则直接开仓（不错过最佳时机）
        # ----------------------------------------------------------------
        if not position_status and macd_30m_trend is not None and macd_5m_trend is not None:
            if macd_30m_trend == macd_5m_trend:
                direct_signal = macd_30m_trend
                log.info(f"📊 [直接开仓检查] 30min={macd_30m_trend}, 5min={macd_5m_trend} 同向，直接开仓信号: {direct_signal.upper()}")
                
                # 上次亏损时需要双重确认
                if position_tracker.get('last_trade_loss'):
                    log.info(f"⚠️ 上次亏损，需双重确认（30min当前柱 + 已收盘柱都要同向）")
                    current_30m_trend = get_macd_trend_30m(df30m)
                    closed_30m_trend = get_closed_30m_trend(df30m)
                    if not (current_30m_trend == direct_signal and closed_30m_trend == direct_signal):
                        log.info(f"❌ 双重确认失败: 30min当前={current_30m_trend}, 30min已收盘={closed_30m_trend}，不开仓")
                    elif current_price:
                        log.info(f"✅ 双重确认成功，直接开仓: {direct_signal.upper()} @ ${current_price:.2f}")
                        success = open_position(direct_signal, current_price, direct_signal)
                        if success:
                            position_status = direct_signal
                elif current_price:
                    log.info(f"📈 直接开仓: {direct_signal.upper()} @ ${current_price:.2f}")
                    success = open_position(direct_signal, current_price, direct_signal)
                    if success:
                        position_status = direct_signal

    # --- 更新 last_execution（面板展示用）---
    last_execution.update({
        'timestamp':               now_str,
        'current_price':           current_price,
        'macd_30m_trend':          macd_30m_trend,
        'macd_5m_trend':           macd_5m_trend,
        'last_5m_macd_direction':  position_tracker['last_5m_macd_direction'],
        'reversal_detected':       reversal_detected,
        'close_triggered':         close_triggered,
        'position':                position_tracker['position'],
        'entry_price':             position_tracker['entry_price'],
        'entry_time': (
            position_tracker['entry_time']
            .astimezone(timezone(timedelta(hours=8)))
            .strftime("%Y-%m-%d %H:%M:%S")
            if position_tracker.get('entry_time') else None
        ),
        'entry_5m_macd_direction': position_tracker['entry_5m_macd_direction'],
    })

    log.info("本次检查完成\n")
    # 不再需要返回值，因为我们直接依赖 position_tracker
