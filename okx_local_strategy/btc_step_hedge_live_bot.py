#!/usr/bin/env python3
"""
ETH 双向逐步加减仓实盘交易机器人 (带 4h 趋势过滤与动态资金管理)
标的: ETH-USDT-SWAP
"""

import os, sys, json, time, logging, ccxt, pandas as pd, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv('/home/johnsontang/okx_local_strategy/okx_config.env') or load_dotenv('/Users/johnsontang/work/bitbuy/okx_local_strategy/okx_config.env')

SYMBOL = 'ETH-USDT-SWAP'
TIMEFRAME_1H, TIMEFRAME_4H = '1h', '4h'
RSI_PERIOD = 6
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
LONG_BUY, LONG_SELL, SHORT_BUY, SHORT_SELL = 30, 80, 70, 30
STOP_LOSS_PCT = 0.08
LEVERAGE = 5
STEP_EQUITY_PCT = 0.15
MAX_POS_STEPS = 3

STATE_FILE = Path('state_btc_step.json')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                    handlers=[logging.FileHandler('btc_step_bot.log', encoding='utf-8'), logging.StreamHandler(sys.stdout)])
log = logging.getLogger('ETHStepBot')

TG_TOKEN = os.getenv('TG_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

def send_telegram(message):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, data={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'})
    except Exception as e: log.error(f"Telegram推送失败: {e}")

exchange = ccxt.okx({
    'apiKey': os.getenv('OKX_API_KEY'), 'secret': os.getenv('OKX_SECRET_KEY'), 'password': os.getenv('OKX_PASSPHRASE'),
    'enableRateLimit': True, 'options': {'defaultType': 'swap'},
})

bot_state = {'long_pos': 0.0, 'long_avg_price': 0.0, 'long_buy_flag': False, 'long_sell_flag': False,
             'short_pos': 0.0, 'short_avg_price': 0.0, 'short_buy_flag': False, 'short_sell_flag': False}

def load_state():
    global bot_state
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f: bot_state = json.load(f)
            log.info(f"状态已加载: 多仓={bot_state.get('long_pos')}, 空仓={bot_state.get('short_pos')}")
        except Exception as e: log.warning(f"加载状态失败: {e}")

def save_state():
    try:
        with open(STATE_FILE, 'w') as f: json.dump(bot_state, f, indent=2)
    except Exception as e: log.warning(f"保存状态失败: {e}")

def calc_rsi(df, period=6):
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    return 100 - (100 / (1 + gain.ewm(alpha=1/period, adjust=False).mean() / loss.ewm(alpha=1/period, adjust=False).mean()))

def calc_macd(df):
    exp1 = df['close'].ewm(span=MACD_FAST, adjust=False).mean()
    exp2 = df['close'].ewm(span=MACD_SLOW, adjust=False).mean()
    return exp1 - exp2, (exp1 - exp2).ewm(span=MACD_SIGNAL, adjust=False).mean(), (exp1 - exp2) - (exp1 - exp2).ewm(span=MACD_SIGNAL, adjust=False).mean()

def fetch_klines(tf, limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(SYMBOL, tf, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_numeric(df['timestamp'])
        for c in ['open', 'high', 'low', 'close', 'volume']: df[c] = pd.to_numeric(df[c])
        return df.sort_values('timestamp').reset_index(drop=True).assign(timestamp=lambda x: pd.to_datetime(x['timestamp'], unit='ms'))
    except Exception as e:
        log.error(f"获取 {tf} K线失败: {e}")
        return None

def execute_order(side, amount, pos_side):
    try:
        order = exchange.create_order(symbol=SYMBOL, type='market', side=side, amount=amount, params={'posSide': pos_side})
        msg = f"✅ 下单成功 [{pos_side}]: {side} {amount} | ID: {order.get('id')}"
        log.info(msg)
        send_telegram(msg)
        return order
    except Exception as e:
        msg = f"❌ 下单失败 [{pos_side} {side}]: {e}"
        log.error(msg)
        send_telegram(msg)
        return None

def run_strategy():
    log.info("=== 策略检查开始 ===")
    df_1h, df_4h = fetch_klines(TIMEFRAME_1H, 100), fetch_klines(TIMEFRAME_4H, 50)
    if df_1h is None or df_4h is None or len(df_1h) < 40: return

    df_4h['dif'], df_4h['dea'], _ = calc_macd(df_4h)
    is_bull = df_4h['dif'].iloc[-1] > df_4h['dea'].iloc[-1]

    df_1h['rsi'] = calc_rsi(df_1h, RSI_PERIOD)
    df_1h['dif'], df_1h['dea'], df_1h['hist'] = calc_macd(df_1h)
    price, rsi = df_1h['close'].iloc[-1], df_1h['rsi'].iloc[-1]
    h, hp = df_1h['hist'].iloc[-1], df_1h['hist'].iloc[-2]
    if pd.isna(rsi) or pd.isna(h) or pd.isna(hp): return

    m_up, m_down = h > hp, h < hp
    # ETH 每次加仓 1 张 (0.1 ETH, 5x杠杆需保证金约 50U)
    step_qty = 1
    max_allowed = step_qty * MAX_POS_STEPS

    # 循环检查推送
    equity = get_equity()
    msg = (f"🔍 策略巡检\n"
           f"💰 账户可用: {equity:.2f} USDT\n"
           f"📊 价格: {price:.2f}\n"
           f"📈 RSI(6): {rsi:.1f}\n"
           f"📉 4h趋势: {'牛市(多)' if is_bull else '熊市(空)'}\n"
           f"📋 持仓: 多={bot_state['long_pos']}, 空={bot_state['short_pos']}\n"
           f"💡 触发条件状态:\n"
           f"- 多头: {'满足' if (rsi < LONG_BUY and is_bull) else '不满足'}\n"
           f"- 空头: {'满足' if (rsi > SHORT_BUY and not is_bull) else '不满足'}")
    send_telegram(msg)

    log.info(f"ETH价格:${price:.2f} | RSI:{rsi:.1f} | 4h牛市:{is_bull} | 每次加仓:{step_qty} 张")

    # 1. 止损检查
    if bot_state['long_pos'] > 0 and (price - bot_state['long_avg_price']) / bot_state['long_avg_price'] <= -STOP_LOSS_PCT:
        execute_order('sell', bot_state['long_pos'], 'long')
        bot_state.update({'long_pos': 0.0, 'long_avg_price': 0.0, 'long_buy_flag': False, 'long_sell_flag': False})
    if bot_state['short_pos'] > 0 and (bot_state['short_avg_price'] - price) / bot_state['short_avg_price'] <= -STOP_LOSS_PCT:
        execute_order('buy', bot_state['short_pos'], 'short')
        bot_state.update({'short_pos': 0.0, 'short_avg_price': 0.0, 'short_buy_flag': False, 'short_sell_flag': False})

    # 2. 多头
    if rsi < LONG_BUY and bot_state['long_pos'] < max_allowed and is_bull: bot_state['long_buy_flag'] = True
    if rsi > LONG_SELL and bot_state['long_pos'] > 0: bot_state['long_sell_flag'] = True

    if bot_state['long_buy_flag'] and m_up and bot_state['long_pos'] < max_allowed and is_bull:
        if execute_order('buy', step_qty, 'long'):
            op, oa = bot_state['long_pos'], bot_state['long_avg_price']
            bot_state['long_avg_price'] = (oa * op + price * step_qty) / (op + step_qty) if op > 0 else price
            bot_state['long_pos'] += step_qty
            bot_state['long_buy_flag'] = False
            send_telegram(f"🟢 多加仓 +{step_qty} 张 | 总仓:{bot_state['long_pos']} | 均价:{bot_state['long_avg_price']:.2f}")

    if bot_state['long_sell_flag'] and m_down and bot_state['long_pos'] > 0:
        rq = min(step_qty, bot_state['long_pos'])
        if execute_order('sell', rq, 'long'):
            bot_state['long_pos'] -= rq
            if bot_state['long_pos'] <= 0: bot_state['long_avg_price'] = 0.0
            bot_state['long_sell_flag'] = False
            send_telegram(f"🔴 多减仓 -{rq} 张 | 剩:{bot_state['long_pos']}")

    # 3. 空头
    if rsi > SHORT_BUY and bot_state['short_pos'] < max_allowed and (not is_bull): bot_state['short_buy_flag'] = True
    if rsi < SHORT_SELL and bot_state['short_pos'] > 0: bot_state['short_sell_flag'] = True

    if bot_state['short_buy_flag'] and m_down and bot_state['short_pos'] < max_allowed and (not is_bull):
        if execute_order('sell', step_qty, 'short'):
            op, oa = bot_state['short_pos'], bot_state['short_avg_price']
            bot_state['short_avg_price'] = (oa * op + price * step_qty) / (op + step_qty) if op > 0 else price
            bot_state['short_pos'] += step_qty
            bot_state['short_buy_flag'] = False
            send_telegram(f"🟠 空加仓 +{step_qty} 张 | 总仓:{bot_state['short_pos']} | 均价:{bot_state['short_avg_price']:.2f}")

    if bot_state['short_sell_flag'] and m_up and bot_state['short_pos'] > 0:
        rq = min(step_qty, bot_state['short_pos'])
        if execute_order('buy', rq, 'short'):
            bot_state['short_pos'] -= rq
            if bot_state['short_pos'] <= 0: bot_state['short_avg_price'] = 0.0
            bot_state['short_sell_flag'] = False
            send_telegram(f"🔵 空减仓 -{rq} 张 | 剩:{bot_state['short_pos']}")

    save_state()
    log.info("=== 策略检查结束 ===\n")

def main():
    msg = "🚀 ETH 双向加减仓实盘机器人已启动 (ETH-USDT-SWAP)"
    log.info(msg)
    send_telegram(msg)
    load_state()
    try:
        exchange.set_leverage(LEVERAGE, SYMBOL, {'posSide': 'long'})
        exchange.set_leverage(LEVERAGE, SYMBOL, {'posSide': 'short'})
    except: pass

    while True:
        try: run_strategy()
        except Exception as e: log.error(f"主循环异常: {e}")
        time.sleep(3600)

if __name__ == '__main__':
    main()
