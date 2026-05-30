#!/usr/bin/env python3
"""
New Strategy Bot - 1H MACD反转开仓 + 4H MACD平仓
强化版：严格要求大小趋势一致才开仓 + 整点后检查支持
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
SYMBOL          = 'BTC-USDT-SWAP'
TARGET_MARGIN   = 20
LEVERAGE        = 7
RSI_LENGTH      = 9

MACD_FAST       = 9
MACD_SLOW       = 21
MACD_SIGNAL     = 60

RSI_OVERBOUGHT  = 70
RSI_OVERSOLD    = 30

STATE_FILE      = Path('state.json')

# ==================== Telegram 推送配置 ====================
TELEGRAM_TOKEN  = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

log.info("=== 1H反转 + 4H平仓 策略已启动（大小趋势一致强化版） ===")

# ==================== Telegram 推送 ====================
def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        params = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.warning(f"TG推送失败: {response.text}")
    except Exception as e:
        log.warning(f"TG推送异常: {e}")

# ==================== 市场活跃时段控制 ====================
def is_market_active() -> bool:
    """比特币高活跃时段：08:00 - 01:59 JST"""
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    hour = now_jst.hour
    return (8 <= hour < 24) or (0 <= hour < 2)


def get_current_session() -> str:
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    hour = now_jst.hour
    if 8 <= hour < 17:
        return "亚洲"
    elif 17 <= hour < 24 or 0 <= hour < 2:
        return "欧美"
    else:
        return "休眠"


exchange = ccxt.okx({
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET_KEY'),
    'password': os.getenv('OKX_PASSPHRASE'),
    'enableRateLimit': True,
    'options': {'defaultType': 'swap', 'marginMode': 'isolated'}
})

# ==================== 状态持久化 ====================
_DEFAULT_TRACKER = {
    'entry_price': None,
    'entry_time': None,
    'position': None,
    'entry_1h_macd_direction': None,
    'last_1h_macd_direction': None,
    'open_contracts': None,
    'trade_history': [],
    'last_trade_loss': False,
}

def save_state():
    try:
        data = {k: v.isoformat() if isinstance(v, datetime) else v for k, v in position_tracker.items()}
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        log.warning(f"保存状态失败: {e}")

def load_state():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            if data.get('entry_time'):
                data['entry_time'] = datetime.fromisoformat(data['entry_time'])
            return data
        except Exception as e:
            log.warning(f"加载状态失败: {e}")
    return dict(_DEFAULT_TRACKER)

position_tracker = load_state()
log.info(f"状态加载完成: position={position_tracker.get('position')}")

# 面板数据
last_execution = {
    'timestamp': None, 'current_price': None,
    'macd_4h_trend': None, 'macd_1h_trend': None,
    'last_1h_macd_direction': None,
    'reversal_detected': False, 'close_triggered': False,
    'position': None, 'entry_price': None,
    'market_session': None, 'market_active': None
}

# ==================== 合约与下单 ====================
_contract_size_cache = {}

def get_contract_size():
    if 'size' in _contract_size_cache:
        return _contract_size_cache['size']
    try:
        markets = exchange.load_markets()
        market = markets.get(SYMBOL) or markets.get(SYMBOL.replace('-', '/'))
        size = float(market.get('contractSize', 0.01))
    except:
        size = 0.01
    _contract_size_cache['size'] = size
    return size

def calculate_contracts(price):
    return round(TARGET_MARGIN * LEVERAGE / (price * get_contract_size()), 4)

def open_position(direction: str, price: float, entry_1h_dir: str):
    try:
        contracts = calculate_contracts(price)
        side = 'buy' if direction == 'long' else 'sell'
        
        log.info(f"⬆️ 尝试开仓 {direction.upper()} | 价格: ${price:.2f} | 张数: {contracts}")

        exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side=side,
            amount=contracts,
            params={'tdMode': 'isolated', 'posSide': direction}
        )

        position_tracker.update({
            'entry_price': float(price),
            'entry_time': datetime.now(timezone.utc),
            'position': direction,
            'open_contracts': contracts,
            'entry_1h_macd_direction': entry_1h_dir
        })
        save_state()
        message = f"✅ 开仓成功\n📈 方向: {direction.upper()}\n💰 价格: ${price:.2f}\n📦 张数: {contracts}"
        log.info(message)
        send_telegram_message(message)
        return True
    except Exception as e:
        log.error(f"❌ 开仓失败: {e}")
        return False

def close_position(reason: str = ""):
    try:
        direction = position_tracker.get('position')
        contracts = position_tracker.get('open_contracts')
        if not direction or not contracts:
            return True

        side = 'sell' if direction == 'long' else 'buy'
        exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side=side,
            amount=contracts,
            params={'tdMode': 'isolated', 'posSide': direction, 'reduceOnly': True}
        )

        position_tracker.update({
            'entry_price': None, 'entry_time': None, 'position': None,
            'entry_1h_macd_direction': None, 'open_contracts': None
        })
        save_state()
        message = f"🔔 平仓成功\n📉 方向: {direction.upper()}\n📋 原因: {reason}"
        log.info(message)
        send_telegram_message(message)
        return True
    except Exception as e:
        log.error(f"❌ 平仓失败: {e}")
        return False

# ==================== K线 & 指标 ====================
def fetch_klines(exch, symbol, timeframe, limit=150):
    ohlcv = exch.fetch_ohlcv(symbol, timeframe, limit=limit)
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

def calculate_indicators(df):
    if len(df) < 50:
        return df
    df['macd_hist'] = calculate_macd_okx(df['close'])
    return df

def get_macd_trend(df, idx=-1):
    if df is None or len(df) < abs(idx) + 1 or 'macd_hist' not in df.columns:
        return None
    cur = df['macd_hist'].iloc[idx]
    prev = df['macd_hist'].iloc[idx - 1]
    if pd.isna(cur) or pd.isna(prev):
        return None
    return 'long' if cur > prev else 'short'

# ==================== 主策略逻辑 ====================
def run_new_strategy_check(_, exch, symbol, fast, slow, signal, rsi_len,
                          is_4h_check=False, is_1h_check=False):
    global last_execution
    now_str = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    active = is_market_active()
    session = get_current_session()

    current_price = None
    try:
        ticker = exch.fetch_ticker(symbol)
        current_price = float(ticker['last'])
    except:
        pass

    df1h = df4h = None
    try:
        df1h = fetch_klines(exch, symbol, '1h', 100)
        df1h = calculate_indicators(df1h)
    except:
        pass
    try:
        df4h = fetch_klines(exch, symbol, '4h', 60)
        df4h = calculate_indicators(df4h)
    except:
        pass

    trend_1h = get_macd_trend(df1h)
    trend_4h = get_macd_trend(df4h)

    log.info(f"[{now_str}] {session} | 1H:{trend_1h} | 4H:{trend_4h} | 活跃:{active}")

    # ==================== 平仓检查 ====================
    if is_4h_check and active and position_tracker.get('position'):
        position = position_tracker.get('position')
        if position and trend_4h and position != trend_4h:
            close_position(f"4H趋势({trend_4h}) 与持仓方向({position})不一致")
            last_execution['close_triggered'] = True

    # ==================== 开仓检查 ====================
    if is_1h_check and active and not position_tracker.get('position') and trend_1h and trend_4h:
        prev_1h = get_macd_trend(df1h, -2)

        if prev_1h and trend_1h != prev_1h:
            reversal_recent = False
            if df1h is not None and len(df1h) >= 4:
                prev_2h = get_macd_trend(df1h, -3)
                prev_3h = get_macd_trend(df1h, -4)
                reversal_recent = (prev_2h != trend_1h) or (prev_3h != trend_1h and prev_2h == trend_1h)
            else:
                reversal_recent = True

            if reversal_recent and trend_4h == trend_1h:
                log.info(f"⚡ 1H反转({prev_1h}→{trend_1h}) + 4H同向({trend_4h}) → 执行开仓")
                if current_price:
                    open_position(trend_1h, current_price, trend_1h)
            elif not reversal_recent:
                log.info(f"⚠️ 1H反转({prev_1h}→{trend_1h})但超过2根K线（追高风险），拒绝开仓")
            elif trend_4h != trend_1h:
                log.info(f"⚠️ 1H反转({prev_1h}→{trend_1h})但4H方向不一致({trend_4h})，拒绝开仓")

    last_execution.update({
        'timestamp': now_str,
        'current_price': current_price,
        'macd_4h_trend': trend_4h,
        'macd_1h_trend': trend_1h,
        'last_1h_macd_direction': position_tracker.get('last_1h_macd_direction'),
        'position': position_tracker.get('position'),
        'entry_price': position_tracker.get('entry_price'),
        'market_session': session,
        'market_active': active
    })
    save_state()