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
SYMBOL          = 'SOL-USDT-SWAP'
TARGET_MARGIN   = 20        # 目标保证金（USDT）
LEVERAGE        = 7
RSI_LENGTH      = 9

MACD_FAST       = 9
MACD_SLOW       = 21
MACD_SIGNAL     = 60

RSI_OVERBOUGHT  = 70    # RSI超买阈值（开多信号时过滤）
RSI_OVERSOLD    = 30    # RSI超卖阈值（开空信号时过滤）

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
    'entry_price':            None,
    'entry_time':             None,
    'position':               None,
    'entry_5m_macd_direction': None,
    'last_5m_macd_direction':  None,
    'open_contracts':          None,
    'trade_history':           [],
    'last_trade_loss':         False,
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

def close_position(reason: str = '') -> bool:
    try:
        actual_direction, actual_contracts = get_actual_position()

        if actual_direction is None:
            log.warning("获取交易所持仓失败，尝试使用本地记录")
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
    min_required = max(slow + signal, rsi_len + 1)
    if len(df) < min_required:
        log.warning(f"数据点不足 [{timeframe}]: 当前{len(df)}根, 需要至少{min_required}根")
        for col in ('macd', 'macd_signal', 'macd_hist', 'rsi'):
            df[col] = float('nan')
        return df

    macd_line, signal_line, histogram = calculate_macd_okx(df['close'].values, fast, slow, signal)
    df['macd'] = macd_line
    df['macd_signal'] = signal_line
    df['macd_hist'] = histogram
    df['rsi'] = calculate_rsi(df['close'].values, rsi_len)
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

def get_macd_trend_30m(df30m):
    if len(df30m) < 3:
        return None
    latest_close_time = df30m['timestamp'].iloc[-1] + timedelta(minutes=30)
    time_until_close = (latest_close_time - now_utc()).total_seconds()
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
    return _macd_trend_from_hist(cur, prv)

def get_closed_30m_trend(df30m):
    if df30m is None or len(df30m) < 3:
        return None
    
    latest_ts = df30m['timestamp'].iloc[-1]
    latest_close_time = latest_ts + timedelta(minutes=30)
    time_until_close = (latest_close_time - now_utc()).total_seconds()
    
    if time_until_close > 0 and time_until_close <= 300:
        idx = -1
        log.info(f"📊 已收盘30min趋势: 使用当前柱（即将走完）")
    else:
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

def get_5m_current_trend(df5m):
    if len(df5m) < 2:
        return None
    return get_macd_trend_okx(df5m, idx=-1)

def get_5m_prev_trend(df5m):
    if len(df5m) < 3:
        return None
    return get_macd_trend_okx(df5m, idx=-2)

# ==================== 核心策略逻辑 ====================
def check_5m_reversal(df5m) -> tuple[str | None, str | None]:
    current_trend = get_5m_current_trend(df5m)
    prev_trend = position_tracker.get('last_5m_macd_direction')
    
    if prev_trend is None:
        prev_trend = get_5m_prev_trend(df5m)

    if current_trend is None or prev_trend is None:
        log.warning("无法获取5min MACD趋势")
        return None, None

    log.info(f"🔍 5min MACD | 上一柱={prev_trend} → 当前柱={current_trend}")

    if current_trend != prev_trend:
        log.info(f"⚡ 5min MACD趋势反转: {prev_trend} → {current_trend}")
        
        if len(df5m) >= 2 and 'rsi' in df5m.columns:
            current_rsi = df5m['rsi'].iloc[-1]
            log.info(f"📊 当前5min RSI: {current_rsi:.2f}")
            
            if current_trend == 'long':
                if current_rsi >= RSI_OVERBOUGHT:
                    log.info(f"❌ RSI({current_rsi:.2f}) >= {RSI_OVERBOUGHT}，过滤开多信号")
                    return None, current_trend
            else:
                if current_rsi <= RSI_OVERSOLD:
                    log.info(f"❌ RSI({current_rsi:.2f}) <= {RSI_OVERSOLD}，过滤开空信号")
                    return None, current_trend
        
        return current_trend, current_trend
    else:
        log.info(f"➡️  5min MACD无反转，方向持续: {current_trend}")
        return None, current_trend

def check_30m_exit(df5m, df30m) -> bool:
    if not position_tracker.get('position'):
        log.info("无持仓，跳过平仓检查")
        return False

    current_30m_trend = get_macd_trend_30m(df30m)
    entry_5m_dir = position_tracker.get('entry_5m_macd_direction')
    open_direction = position_tracker.get('position')

    if current_30m_trend is None:
        log.warning("无法获取当前30min MACD趋势，使用5min趋势作为兜底")
        current_5m_trend = get_5m_current_trend(df5m)
        if current_5m_trend is None:
            log.warning("5min趋势也无法获取，跳过平仓检查")
            return False
        if open_direction == 'long' and current_5m_trend == 'short':
            log.info(f"📊 兜底平仓: 持仓long，但5min趋势变short")
            return True
        elif open_direction == 'short' and current_5m_trend == 'long':
            log.info(f"📊 兜底平仓: 持仓short，但5min趋势变long")
            return True
        else:
            return False

    if entry_5m_dir is None:
        log.warning("开仓5min方向未记录，跳过平仓检查")
        return False

    log.info(f"📊 平仓检查 | 当前30min趋势={current_30m_trend} | 开仓时5min方向={entry_5m_dir}")

    should_close = False
    if entry_5m_dir == 'long' and current_30m_trend == 'short':
        should_close = True
        reason = "开仓时5min=long，但当前30min=short"
    elif entry_5m_dir == 'short' and current_30m_trend == 'long':
        should_close = True
        reason = "开仓时5min=short，但当前30min=long"
    else:
        reason = "30min趋势与开仓时5min方向一致，继续持仓"

    log.info(f"平仓判断: {reason}")

    if should_close:
        log.info(f"⚠️ 触发平仓")
        return True
    else:
        log.info(f"✅ 继续持仓")
        return False

# ==================== 策略检查入口 ====================
def run_strategy_check(is_29_30_check=False, is_04_30_check=False):
    now_str = cst_str()
    position_status = position_tracker.get('position')

    current_price = None
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        current_price = float(ticker['last'])
    except Exception as e:
        log.error(f"获取价格失败: {e}")

    df30m = None
    df5m = None
    try:
        df30m = fetch_klines(exchange, SYMBOL, '30m', 120)
        df30m = calculate_indicators(df30m, '30m', MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH)
    except Exception as e:
        log.error(f"获取30m K线失败: {e}")

    try:
        df5m = fetch_klines(exchange, SYMBOL, '5m', 100)
        df5m = calculate_indicators(df5m, '5m', MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH)
    except Exception as e:
        log.error(f"获取5m K线失败: {e}")

    macd_30m_trend = get_macd_trend_30m(df30m) if df30m is not None else None
    macd_5m_trend = get_macd_trend_okx(df5m) if df5m is not None else None

    log.info(f"\n[{now_str}] 策略检查 | 29:30={is_29_30_check} | 04:30={is_04_30_check}")
    log.info(f"30min={macd_30m_trend} | 5min={macd_5m_trend} | 价格={'$'+f'{current_price:.2f}' if current_price else '未获取'}")
    log.info(f"持仓={position_status or '无'} | last_5m={position_tracker['last_5m_macd_direction']}")

    if is_29_30_check and df5m is not None and df30m is not None:
        log.info("\n[29:55] 检查平仓条件...")
        should_close = check_30m_exit(df5m, df30m)
        if should_close:
            if close_position('30min方向与开仓时5min方向不同'):
                position_status = None

    if is_04_30_check and df5m is not None:
        log.info("\n[04:30] 检测5min MACD反转...")
        open_signal, current_5m = check_5m_reversal(df5m)
        
        if current_5m is not None:
            position_tracker['last_5m_macd_direction'] = current_5m
            save_state()

        if open_signal:
            if not position_status:
                trend_confirm = False
                if macd_30m_trend == open_signal:
                    trend_confirm = True
                    log.info(f"✅ 30min趋势确认: {macd_30m_trend.upper()} 与开仓方向一致")
                else:
                    log.info(f"❌ 30min趋势: {macd_30m_trend.upper()} 与开仓方向不一致")
                
                double_confirm = False
                if position_tracker.get('last_trade_loss'):
                    log.info(f"⚠️ 上次亏损，需双重确认")
                    current_30m_trend = get_macd_trend_30m(df30m)
                    closed_30m_trend = get_closed_30m_trend(df30m)
                    if current_30m_trend == open_signal and closed_30m_trend == open_signal:
                        double_confirm = True
                        log.info(f"✅ 双重确认成功")
                    else:
                        log.info(f"❌ 双重确认失败")
                
                should_open = trend_confirm and (not position_tracker.get('last_trade_loss') or double_confirm)
                
                if should_open:
                    confirm_type = "双重确认" if position_tracker.get('last_trade_loss') else "趋势确认"
                    log.info(f"📈 反转信号: {open_signal.upper()}，{confirm_type} → 开仓")
                    if current_price:
                        success = open_position(open_signal, current_price, open_signal)
                        if success:
                            log.info(f"✅ 开仓完成: {open_signal.upper()} @ ${current_price:.2f}")
                    else:
                        log.error("❌ 无法获取价格，开仓取消")
                else:
                    reason = "30min趋势不确认" if not trend_confirm else "双重确认失败"
                    log.info(f"⚠️ 反转信号: {open_signal.upper()}，但{reason} → 不开仓")
            else:
                log.info(f"⚠️ 反转信号: {open_signal.upper()}，但当前有持仓({position_status}) → 不开仓")
        else:
            log.info("➡️  无反转信号，持仓不变")

    log.info("本次检查完成\n")

# ==================== 主循环 ====================
def main():
    log.info("=== OKX 本地版交易策略机器人 已启动 ===")
    log.info(f"标的: {SYMBOL} | 目标保证金: {TARGET_MARGIN} USDT | 杠杆: {LEVERAGE}x")
    log.info(f"入场: 5min MACD反转 @ 04:30 | 出场: 30min MACD方向不同 @ 29:55")
    log.info(f"状态已加载: position={position_tracker['position']} | last_5m={position_tracker['last_5m_macd_direction']}")

    _5m_done_ts = None
    _30m_done_ts = None

    while True:
        now_utc = datetime.now(timezone.utc)
        now_cst = now_utc.astimezone(timezone(timedelta(hours=8)))
        m, s = now_cst.minute, now_cst.second

        if not position_tracker.get('position') and ((m % 5 == 4 and s >= 30) or (m % 5 == 0 and s <= 10)):
            if not _5m_done_ts or now_utc > _5m_done_ts + timedelta(minutes=4):
                log.info(f"[{now_cst.strftime('%H:%M:%S')}] 📊 检查5min开仓条件...")
                run_strategy_check(is_29_30_check=False, is_04_30_check=True)
                _5m_done_ts = now_utc

        if position_tracker.get('position') and s >= 55 and (m % 30 == 29 or m % 30 == 59):
            if not _30m_done_ts or now_utc > _30m_done_ts + timedelta(minutes=29):
                log.info(f"[{now_cst.strftime('%H:%M:%S')}] 📊 检查30min平仓条件...")
                run_strategy_check(is_29_30_check=True, is_04_30_check=False)
                _30m_done_ts = now_utc

        time.sleep(1)

if __name__ == "__main__":
    main()
