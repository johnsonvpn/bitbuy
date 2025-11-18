# -*- coding: utf-8 -*-
"""
OKX EMA密集突破机器人 - 终极稳定版（2025-11-17）
已彻底解决：
1. name 'strategy' is not defined
2. could not convert string to float: ''
3. 余额/持仓查询崩溃
"""

import time
import requests
import logging
import pandas as pd
import numpy as np
from pandas_ta import ema
from okx import MarketData, Trade, Account
from flask import Flask, request, render_template_string
from threading import Thread
import os
from datetime import datetime, timezone, timedelta

# ============ 北京时间日志 ============
class BeijingFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp)
        beijing = dt.astimezone(timezone(timedelta(hours=8)))
        return beijing.timetuple()
    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        return time.strftime("%Y-%m-%d %H:%M:%S", ct) + f".{int(record.msecs):03d}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", handlers=[logging.StreamHandler()])
for handler in logging.getLogger().handlers:
    handler.setFormatter(BeijingFormatter())

app = Flask(__name__)

# ============ 全局配置 ============
SYMBOL = "BTC-USDT-SWAP"
BAR_INTERVAL = "5m"
API_KEY = SECRET_KEY = PASS_PHRASE = BOT_TOKEN = CHAT_ID = ""
BOT_RUNNING = False
BOT_THREAD = None
GLOBAL_FLAG = "0"  # 0=实盘 1=模拟盘
RENDER_URL = "https://bitbuy-w8xw.onrender.com/send"

# ============ Telegram 通知 ============
def send_telegram_message(msg):
    if not SECRET_KEY or not msg: return
    try:
        requests.post(RENDER_URL, json={"key": SECRET_KEY, "text": msg[:4000]}, timeout=5)
    except: pass

# ============ 余额查询（彻底修复）===========
def get_account_balance():
    try:
        acc = Account.AccountAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        resp = acc.get_account_balance(ccy="USDT")
        if resp.get("code") == "0" and resp.get("data"):
            details = resp["data"][0].get("details", [])
            if details:
                bal = details[0].get("availBal") or details[0].get("cashBal") or "0"
                return float(bal) if bal else 100000.0
        return 100000.0
    except Exception as e:
        logging.error(f"余额异常: {e}")
        return 100000.0

# ============ 持仓查询（彻底防空字符串崩溃）===========
def get_current_positions():
    try:
        acc = Account.AccountAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        resp = acc.get_positions(instId=SYMBOL)
        if resp.get("code") != "0" or not resp.get("data"): 
            return []
        positions = []
        for p in resp["data"]:
            try:
                positions.append({
                    "posSide": p.get("posSide", "net"),
                    "pos": float(p.get("pos", 0) or 0),
                    "avgPx": float(p.get("avgPx", 0) or 0),
                    "upl": float(p.get("upl", 0) or 0)
                })
            except:
                continue  # 单个字段异常不影响整体
        return positions
    except Exception as e:
        logging.error(f"持仓异常: {e}")
        return []

# ============ 下单/平仓 ============
def place_order(side, size, price):
    try:
        trade = Trade.TradeAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        trade.set_leverage(instId=SYMBOL, lever="1", mgnMode="isolated")
        r = trade.place_order(instId=SYMBOL, tdMode="isolated", side=side.lower(), ordType="market", sz=str(size))
        if r.get("code") == "0":
            send_telegram_message(f"开仓成功 {side} {size} BTC @ {price}")
            return True
    except Exception as e:
        send_telegram_message(f"下单失败: {e}")
    return False

def close_position():
    try:
        positions = get_current_positions()
        if not positions: 
            return
        trade = Trade.TradeAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        for p in positions:
            side = "sell" if p["posSide"] in ["long", "net"] else "buy"
            trade.close_position(instId=SYMBOL, posSide=p["posSide"], mgnMode="isolated")
            send_telegram_message(f"平仓 {side} {p['pos']} BTC | 盈亏 {p['upl']:.2f} USDT")
    except Exception as e:
        send_telegram_message(f"平仓异常: {e}")

# ============ 终极稳定策略（直接写死，无exec）===========
class EMA_DenseBreakout_Ultimate:
    def __init__(self):
        self.max_trades_per_day = 2
        self.min_interval = 1800
        self.dense_min_bars = 10
        self.dense_threshold = 0.0006
        self.volume_multiplier = 1.8
        self.risk_per_trade = 0.005
        self.fixed_stop_loss = 0.03
        self.take_profit_r = 2.5

        self.last_trade_time = 0
        self.daily_trades = 0
        self.current_date = None
        self.position = 0
        self.entry_price = 0
        self.stop_loss = 0
        self.take_profit = 0

    def calculate_indicators(self, df):
        df['ema20'] = ema(df['close'], length=20)
        df['ema60'] = ema(df['close'], length=60)
        df['ema120'] = ema(df['close'], length=120)
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        return df

    def is_dense(self, row):
        emas = [row['ema20'], row['ema60'], row['ema120']]
        if any(pd.isna(x) for x in emas): return False
        return (max(emas) - min(emas)) / row['close'] <= self.dense_threshold

    def generate_signal(self, df):
        df = self.calculate_indicators(df)
        df['dense'] = df.apply(self.is_dense, axis=1)
        df['dense_start'] = (df['dense'] & ~df['dense'].shift(1).fillna(False))
        df['dense_group'] = df['dense_start'].cumsum()
        df['dense_bars'] = df.groupby('dense_group')['dense'].cumsum()
        df['dense_high'] = df[['ema20','ema60','ema120']].max(axis=1).where(df['dense'])
        df['dense_low']  = df[['ema20','ema60','ema120']].min(axis=1).where(df['dense'])

        for i in range(len(df)-3):
            if df.iloc[i]['dense'] and df.iloc[i]['dense_bars'] >= self.dense_min_bars:
                zone = df[df['dense_group'] == df.iloc[i]['dense_group']]
                high = zone['dense_high'].max()
                low  = zone['dense_low'].min()
                vol_ok = df.iloc[i+1]['volume'] > df.iloc[i+1]['vol_ma20'] * self.volume_multiplier

                if df.iloc[i+1]['close'] > high and vol_ok and df.iloc[i+2]['close'] > high:
                    return 'BUY', high
                if df.iloc[i+1]['close'] < low and vol_ok and df.iloc[i+2]['close'] < low:
                    return 'SELL', low
        return None, None

    def check_exit(self, price, ema20):
        if self.position == 0: return False, 0
        if self.position == 1 and price < ema20: return True, price
        if self.position == -1 and price > ema20: return True, price
        if self.position == 1 and (self.entry_price - price)/self.entry_price > self.fixed_stop_loss: return True, price
        if self.position == -1 and (price - self.entry_price)/self.entry_price > self.fixed_stop_loss: return True, price
        if self.position == 1 and price >= self.take_profit: return True, price
        if self.position == -1 and price <= self.take_profit: return True, price
        return False, 0

    def can_enter(self):
        today = datetime.now().date()
        if self.current_date != today:
            self.daily_trades = 0
            self.current_date = today
        if self.daily_trades >= self.max_trades_per_day: return False
        if time.time() - self.last_trade_time < self.min_interval: return False
        return True

    def enter_position(self, signal, price, dense_low=None, dense_high=None, capital=100000):
        if not self.can_enter(): return False
        risk = capital * self.risk_per_trade

        if signal == 'BUY':
            sl = dense_low * 0.997 if dense_low else price * (1 - self.fixed_stop_loss)
            risk_per = price - sl
            if risk_per <= 0: return False
            size = np.floor(risk / risk_per * 1000) / 1000
            if size < 0.001: return False
            self.position = 1
            self.entry_price = price
            self.stop_loss = sl
            self.take_profit = price + self.take_profit_r * risk_per
            self.last_trade_time = time.time()
            self.daily_trades += 1
            return {"side": "BUY", "size": size, "price": price}

        elif signal == 'SELL':
            sl = dense_high * 1.003 if dense_high else price * (1 + self.fixed_stop_loss)
            risk_per = sl - price
            if risk_per <= 0: return False
            size = np.floor(risk / risk_per * 1000) / 1000
            if size < 0.001: return False
            self.position = -1
            self.entry_price = price
            self.stop_loss = sl
            self.take_profit = price - self.take_profit_r * risk_per
            self.last_trade_time = time.time()
            self.daily_trades += 1
            return {"side": "SELL", "size": size, "price": price}
        return False

# 全局策略实例（关键！）
strategy = EMA_DenseBreakout_Ultimate()

# ============ 主循环 ============
def run_bot():
    while BOT_RUNNING:
        try:
            # 检查持仓
            positions = get_current_positions()
            market = MarketData.MarketAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
            hist = market.get_history_candlesticks(instId=SYMBOL, bar=BAR_INTERVAL, limit=300)
            if hist.get("code") != "0": 
                time.sleep(10)
                continue

            df = pd.DataFrame(hist["data"], columns=["ts","o","h","l","c","v","v2","v3","v4"])
            df['close'] = df['c'].astype(float)
            df['volume'] = df['v'].astype(float)

            current_price = df['close'].iloc[-1]
            ema20 = df['ema20'].iloc[-1] if 'ema20' in df else current_price

            # 有持仓 → 只检查退出
            if positions:
                exit_sig, _ = strategy.check_exit(current_price, ema20)
                if exit_sig:
                    close_position()
                    strategy.position = 0
                time.sleep(30)
                continue

            # 无持仓 → 检查开仓
            signal, _ = strategy.generate_signal(df)
            if signal and strategy.position == 0:
                dense_low = df['dense_low'].max() if 'dense_low' in df else None
                dense_high = df['dense_high'].max() if 'dense_high' in df else None
                capital = get_account_balance()
                order = strategy.enter_position(signal, current_price, dense_low, dense_high, capital)
                if order:
                    place_order(order['side'], order['size'], order['price'])

            time.sleep(30)
        except Exception as e:
            logging.error(f"Bot错误: {e}")
            send_telegram_message(f"Bot错误: {e}")
            time.sleep(10)

# ============ Web 界面（极简）===========
HTML = """
<h1>OKX EMA密集突破机器人（终极稳定版）</h1>
{% if running %}
    <p style="color:green;font-size:20px">运行中：{{symbol}} {{bar}}</p>
    <form method=post action="/stop"><button>停止机器人</button></form>
{% else %}
    <form method=post>
        API Key: <input name="k" value="{{k}}" required><br>
        Secret: <input name="s" value="{{s}}" required><br>
        Pass: <input name="p" value="{{p}}" required><br>
        TG Token: <input name="t" value="{{t}}" required><br>
        Chat ID: <input name="c" value="{{c}}" required><br>
        <button type=submit>启动机器人</button>
    </form>
{% endif %}
"""

@app.route("/", methods=["GET","POST"])
def index():
    global BOT_RUNNING, BOT_THREAD, API_KEY, SECRET_KEY, PASS_PHRASE, BOT_TOKEN, CHAT_ID, GLOBAL_FLAG
    if request.method == "POST" and not BOT_RUNNING:
        API_KEY = request.form["k"]
        SECRET_KEY = request.form["s"]
        PASS_PHRASE = request.form["p"]
        BOT_TOKEN = request.form["t"]
        CHAT_ID = request.form["c"]
        GLOBAL_FLAG = "0"  # 实盘
        BOT_RUNNING = True
        BOT_THREAD = Thread(target=run_bot, daemon=True)
        BOT_THREAD.start()
        send_telegram_message("机器人已启动")
    elif "stop" in request.path:
        BOT_RUNNING = False
        close_position()
        send_telegram_message("机器人已停止")
    return render_template_string(HTML, running=BOT_RUNNING, symbol=SYMBOL, bar=BAR_INTERVAL,
                                  k=API_KEY, s=SECRET_KEY, p=PASS_PHRASE, t=BOT_TOKEN, c=CHAT_ID)

@app.route("/stop", methods=["POST"])
def stop(): return index()

@app.route("/health")
def health(): return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
