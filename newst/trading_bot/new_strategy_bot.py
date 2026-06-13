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

# ==================== 配置 ====================
SYMBOL          = 'BTC/USDT:USDT'
TARGET_MARGIN   = 20
LEVERAGE        = 7
RSI_LENGTH      = 9

MACD_FAST       = 9
MACD_SLOW       = 21
MACD_SIGNAL     = 60

RSI_OVERBOUGHT  = 70
RSI_OVERSOLD    = 30

# 支持环境变量配置数据目录
DATA_DIR = Path(os.getenv('DATA_DIR', '.'))
DATA_DIR.mkdir(exist_ok=True)
STATE_FILE = DATA_DIR / 'state.json'

# ==================== 日志 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(DATA_DIR / 'bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

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


# ==================== 交易所初始化与 API 检查 ====================
def create_exchange():
    """创建交易所连接并进行启动前检查"""
    log.info("🔍 正在初始化交易所连接...")
    
    api_key = os.getenv('BINANCE_API_KEY')
    api_secret = os.getenv('BINANCE_SECRET_KEY')
    
    if not api_key or not api_secret:
        log.error("❌ API密钥未配置！请在 binance_config.env 中设置 BINANCE_API_KEY 和 BINANCE_SECRET_KEY")
        send_telegram_message("❌ 机器人启动失败：API密钥未配置")
        raise RuntimeError("API密钥未配置")
    
    exchange = ccxt.binance({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'options': {'defaultType': 'future', 'defaultMarginMode': 'isolated'}
    })
    
    # 测试 API 连接
    log.info("🔍 正在测试 API 连接...")
    try:
        # 尝试获取账户信息来验证连接
        balance = exchange.fetch_balance()
        log.info("✅ API 连接成功！")
        send_telegram_message("✅ 机器人启动成功！API连接正常")
        return exchange
    except Exception as e:
        error_msg = f"❌ API 连接失败: {str(e)}"
        log.error(error_msg)
        send_telegram_message(error_msg)
        raise RuntimeError(error_msg)

# 创建交易所连接（启动时检查）
exchange = create_exchange()

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

        # Binance 合约开仓
        exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side=side,
            amount=contracts,
            params={'positionSide': direction}
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
        # Binance 合约平仓（减仓）
        exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side=side,
            amount=contracts,
            params={'positionSide': direction, 'reduceOnly': True}
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
    s = pd.Series(df['close'].values)
    # 计算 MACD 三要素
    ema_fast = s.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = s.ewm(span=MACD_SLOW, adjust=False).mean()
    df['macd_dif'] = ema_fast - ema_slow  # DIF 线
    df['macd_dea'] = df['macd_dif'].ewm(span=MACD_SIGNAL, adjust=False).mean()  # DEA 线 (Signal)
    df['macd_hist'] = (df['macd_dif'] - df['macd_dea']) * 2  # MACD 柱
    return df

def get_macd_trend(df, idx=-1):
    """基于动能变化的趋势判断"""
    if df is None or len(df) < abs(idx) + 1 or 'macd_hist' not in df.columns:
        return None
    
    # 获取当前和前一根K线的HIST值
    cur_hist = df['macd_hist'].iloc[idx]
    prev_hist = df['macd_hist'].iloc[idx - 1]
    
    if pd.isna(cur_hist) or pd.isna(prev_hist):
        return None
    
    # 动能变化判断
    if cur_hist > prev_hist:
        # 动能增强 → 看多
        return 'long'
    elif cur_hist < prev_hist:
        # 动能减弱 → 看空
        return 'short'
    else:
        # 没有变化，看DIF位置
        cur_dif = df['macd_dif'].iloc[idx]
        return 'long' if cur_dif > 0 else 'short'

def get_macd_details(df, idx=-1):
    """获取 MACD 详细数值用于调试"""
    if df is None or len(df) < abs(idx) or 'macd_dif' not in df.columns:
        return None, None, None, None
    kline_time = df['timestamp'].iloc[idx].tz_convert(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    dif = df['macd_dif'].iloc[idx]
    dea = df['macd_dea'].iloc[idx]
    hist = df['macd_hist'].iloc[idx]
    return kline_time, dif, dea, hist

# ==================== 主策略逻辑 ====================
def run_new_strategy_check(_, exch, symbol, fast, slow, signal, rsi_len,
                          is_4h_check=False, is_1h_check=False):
    global last_execution
    # 统一使用 JST 时间显示
    now_str = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
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

    # 使用上一根已经走完的K线判断趋势（idx=-2）
    trend_1h = get_macd_trend(df1h, idx=-2)
    trend_4h = get_macd_trend(df4h, idx=-2)

    # 获取 MACD 详细数值用于调试（也使用上一根走完的K线）
    t1h_time, dif_1h, dea_1h, hist_1h = get_macd_details(df1h, idx=-2)
    t4h_time, dif_4h, dea_4h, hist_4h = get_macd_details(df4h, idx=-2)

    # 调试：打印数据框的最后几行时间戳
    if df1h is not None and len(df1h) >= 5:
        log.debug(f"🔍 1H K线时间戳(最后5根):")
        for i in range(-5, 0):
            ts = df1h['timestamp'].iloc[i].tz_convert(timezone(timedelta(hours=9))).strftime("%H:%M")
            log.debug(f"   idx={i}: {ts}")

    log.info(f"[{now_str}] {session} | 1H:{trend_1h}({t1h_time}) | 4H:{trend_4h}({t4h_time}) | 活跃:{active}")
    log.info(f"🔍 MACD详情: 1H=DIF:{dif_1h:.4f} DEA:{dea_1h:.4f} HIST:{hist_1h:.4f} | 4H=DIF:{dif_4h:.4f} DEA:{dea_4h:.4f} HIST:{hist_4h:.4f} | 价格:${current_price:.2f}")

    # ==================== 平仓检查 ====================
    if is_4h_check and active and position_tracker.get('position'):
        position = position_tracker.get('position')
        if position and trend_4h and position != trend_4h:
            # 构建平仓推送消息
            tg_msg = f"📊 *平仓检查报告*\n\n"
            tg_msg += f"🎯 交易对: `{symbol}`\n"
            tg_msg += f"💰 当前价格: `${current_price:.2f}`\n\n"
            tg_msg += f"📈 *持仓信息*\n"
            tg_msg += f"• 持仓方向: `{position}`\n"
            tg_msg += f"• 开仓价格: `${position_tracker.get('entry_price', 'N/A')}`\n\n"
            tg_msg += f"🔍 *平仓条件*\n"
            tg_msg += f"• 4H趋势: `{trend_4h}`\n"
            tg_msg += f"• 趋势一致: ❌ 否 (持仓={position}, 4H={trend_4h})\n\n"
            tg_msg += f"✅ *执行平仓*\n"
            tg_msg += f"原因: 4H趋势与持仓方向不一致\n"
            
            close_position(f"4H趋势({trend_4h}) 与持仓方向({position})不一致")
            last_execution['close_triggered'] = True
            
            # 发送TG推送
            send_telegram_message(tg_msg)

    # ==================== 开仓检查 ====================
    if is_1h_check and active and not position_tracker.get('position') and trend_1h and trend_4h:
        # 获取时间戳（对应 idx=-2）
        prev_1h_ts = df1h['timestamp'].iloc[-3].tz_convert(timezone(timedelta(hours=9))).strftime("%H:%M") if df1h is not None and len(df1h) > 2 else "N/A"
        prev_2h_ts = df1h['timestamp'].iloc[-4].tz_convert(timezone(timedelta(hours=9))).strftime("%H:%M") if df1h is not None and len(df1h) > 3 else "N/A"
        prev_3h_ts = df1h['timestamp'].iloc[-5].tz_convert(timezone(timedelta(hours=9))).strftime("%H:%M") if df1h is not None and len(df1h) > 4 else "N/A"

        # 构建基础推送消息
        tg_msg = f"📊 *策略检查报告*\n\n"
        tg_msg += f"🎯 交易对: `{symbol}`\n"
        tg_msg += f"💰 当前价格: `${current_price:.2f}`\n\n"
        tg_msg += f"📈 *MACD趋势*\n"
        tg_msg += f"• 1H趋势: `{trend_1h}` ({t1h_time})\n"
        tg_msg += f"• 4H趋势: `{trend_4h}` ({t4h_time})\n\n"
        tg_msg += f"🔍 *开仓条件检查*\n"

        # 检查1H反转：当前趋势(idx=-2) vs 前一根趋势(idx=-3)
        prev_1h = get_macd_trend(df1h, -3) if df1h is not None and len(df1h) > 2 else None
        has_1h_reversal = prev_1h is not None and trend_1h != prev_1h
        
        # 检查4H反转：当前趋势(idx=-2) vs 前一根趋势(idx=-3)
        prev_4h = get_macd_trend(df4h, -3) if df4h is not None and len(df4h) > 2 else None
        has_4h_reversal = prev_4h is not None and trend_4h != prev_4h

        if has_1h_reversal:
            # 1H反转逻辑（原逻辑）
            reversal_recent = False
            prev_2h = get_macd_trend(df1h, -4) if df1h is not None and len(df1h) >= 4 else None
            prev_3h = get_macd_trend(df1h, -5) if df1h is not None and len(df1h) >= 5 else None
            if df1h is not None and len(df1h) >= 6:
                reversal_recent = (prev_1h != prev_2h) or (prev_2h != prev_3h and prev_1h == prev_2h)
            else:
                reversal_recent = True

            log.info(f"📋 反转分析: 当前({df1h_ts})={trend_1h} vs 上根({prev_1h_ts})={prev_1h} vs 上上根({prev_2h_ts})={prev_2h} vs 上上上根({prev_3h_ts})={prev_3h}")

            tg_msg += f"• 1H反转: ✅ `{prev_1h}` → `{trend_1h}`\n"
            tg_msg += f"• 4H反转: {'✅ 有' if has_4h_reversal else '❌ 无'}\n"
            tg_msg += f"• 反转时效: {'✅ 新鲜' if reversal_recent else '⚠️ 过期'}\n"
            tg_msg += f"• 4H一致: {'✅ 是' if trend_4h == trend_1h else '❌ 否'}\n\n"

            if reversal_recent and trend_4h == trend_1h:
                log.info(f"⚡ 1H反转({prev_1h}→{trend_1h}) + 4H同向({trend_4h}) → 执行开仓")
                tg_msg += f"✅ *开仓执行*\n"
                tg_msg += f"方向: `{trend_1h}`\n"
                if current_price:
                    open_position(trend_1h, current_price, trend_1h)
                    tg_msg += f"状态: ✅ 成功\n"
            elif not reversal_recent:
                log.info(f"⚠️ 1H反转({prev_1h}→{trend_1h})但超过2根K线（追高风险），拒绝开仓")
                tg_msg += f"❌ *拒绝开仓*\n"
                tg_msg += f"原因: 反转超过2根K线，追高风险\n"
            elif trend_4h != trend_1h:
                log.info(f"⚠️ 1H反转({prev_1h}→{trend_1h})但4H方向不一致({trend_4h})，拒绝开仓")
                tg_msg += f"❌ *拒绝开仓*\n"
                tg_msg += f"原因: 4H趋势不一致 (1H={trend_1h}, 4H={trend_4h})\n"
        elif has_4h_reversal and trend_1h == trend_4h:
            # 4H反转 + 1H同向也可以开仓
            log.info(f"⚡ 4H反转({prev_4h}→{trend_4h}) + 1H同向({trend_1h}) → 执行开仓")
            
            tg_msg += f"• 1H反转: {'✅ 有' if has_1h_reversal else '❌ 无'}\n"
            tg_msg += f"• 4H反转: ✅ `{prev_4h}` → `{trend_4h}`\n"
            tg_msg += f"• 1H一致: ✅ 是\n\n"
            tg_msg += f"✅ *开仓执行*\n"
            tg_msg += f"方向: `{trend_4h}`\n"
            if current_price:
                open_position(trend_4h, current_price, trend_4h)
                tg_msg += f"状态: ✅ 成功\n"
        else:
            tg_msg += f"• 1H反转: {'✅ 有' if has_1h_reversal else '❌ 无'}\n"
            tg_msg += f"• 4H反转: {'✅ 有' if has_4h_reversal else '❌ 无'}\n"
            if prev_1h:
                tg_msg += f"  1H当前趋势 `{trend_1h}` 与前一根 `{prev_1h}` 相同\n"
            tg_msg += f"\n⏸️ *不满足开仓条件*\n"
            if not has_1h_reversal and not has_4h_reversal:
                tg_msg += f"原因: 未检测到1H或4H趋势反转\n"
            elif trend_1h != trend_4h:
                tg_msg += f"原因: 1H趋势({trend_1h})与4H趋势({trend_4h})不一致\n"

        # 发送TG推送
        send_telegram_message(tg_msg)

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