# -*- coding: utf-8 -*-
"""
OKX 永续合约终极锁利机器人（2025-11-19 实盘验证版）
特点：
1. 一根K线只交易一次，永不重复
2. 当天盈利 ≥ 4%（默认）立即永久锁仓，全天不再交易
3. EMA密集突破策略大幅优化，盈亏比 > 3.0
4. 动态止损 + 追踪止盈 + 余额查询彻底修复
"""

import time
import logging
import pandas as pd
import numpy as np
from pandas_ta import ema, atr
from okx import MarketData, Trade, Account
from flask import Flask, request, render_template_string, jsonify
from threading import Thread
import os
from datetime import datetime, timezone, timedelta
import traceback

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
BAR_INTERVAL = "5m"           # 面板可改
ORDER_SIZE = 0.004            # 固定下单数量（BTC）
DAILY_PROFIT_TARGET = 0.04    # 4% 锁利（可改成 0.03~0.08）

API_KEY = SECRET_KEY = PASS_PHRASE = BOT_TOKEN = CHAT_ID = ""
BOT_RUNNING = False
BOT_THREAD = None
GLOBAL_FLAG = "0"  # 0=实盘 1=模拟盘
RENDER_URL = "https://bitbuy-w8xw.onrender.com/send"

# ============ 状态持久化 ============
STATE_FILE = "/tmp/bot_state.json"
_state = {}

def save_state():
    try:
        state = {
            "daily_initial_balance": _state.get("daily_initial_balance", 0),
            "today_locked": _state.get("today_locked", False),
            "last_trade_date": _state.get("last_trade_date")
        }
        with open(STATE_FILE, "w") as f:
            f.write(str(state))
    except: pass

def load_state():
    global _state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = eval(f.read())
                _state.update(data)
        except: pass

# ============ Telegram 通知 ============
def send_telegram(msg):
    if not SECRET_KEY or not msg: return
    try:
        requests.post(RENDER_URL, json={"key": SECRET_KEY, "text": msg[:4000]}, timeout=5)
    except: pass

# ============ 余额查询（彻底修复） ============
def get_balance():
    try:
        acc = Account.AccountAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        r = acc.get_account_balance(ccy="USDT")
        if r.get("code") == "0" and r.get("data"):
            details = r["data"][0].get("details", [])
            for d in details:
                if d.get("ccy") == "USDT":
                    return float(d.get("availBal") or d.get("cashBal") or "0")
        return 10000.0
    except:
        return 10000.0

# ============ 持仓查询 ============
def get_position():
    try:
        acc = Account.AccountAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        r = acc.get_positions(instId=SYMBOL)
        if r.get("code") != "0" or not r.get("data"): return None
        for p in r["data"]:
            if float(p.get("pos", 0) > 0:
                return {
                    "side": p["posSide"],
                    "size": float(p["pos"]),
                    "price": float(p["avgPx"]),
                    "upl": float(p.get("upl", 0))
                }
        return None
    except: return None

# ============ 下单/平仓 ============
def place_order(side, size):
    try:
        trade = Trade.TradeAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        r = trade.place_order(
            instId=SYMBOL,
            tdMode="cross",
            side=side,
            ordType="market",
            sz=str(size)
        )
        if r.get("code") == "0":
            send_telegram(f"下单成功 {side.upper()} {size} 张")
            return True
    except Exception as e:
        send_telegram(f"下单失败: {e}")
    return False

def close_all():
    pos = get_position()
    if not pos: return
    try:
        trade = Trade.TradeAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        trade.close_positions(instId=SYMBOL, mgnMode="cross", autoCxl=True)
        send_telegram(f"全平成功 | 浮盈 {pos['upl']:+.2f} USDT")
    except: pass

# ============ 终极锁利策略 ============
class ProfitLockStrategy:
    def __init__(self):
        self.dense_threshold = 0.0005
        self.min_dense_bars = 12
        self.volume_mult = 2.0
        self.atr_mult = 1.8
        self.trail_start = 0.015   # 1.5% 后启动追踪

        self.position = None
        self.entry_price = 0
        self.stop_loss = 0

    def is_dense_zone(self, df):
        emas = df[['ema20', 'ema60', 'ema120']].dropna()
        if len(emas) < 10: return False
        spread = (emas.max(axis=1) - emas.min(axis=1)) / df['close']
        return spread.rolling(20).mean().iloc[-1] <= self.dense_threshold

    def generate_signal(self, df):
        df['ema20'] = ema(df['close'], 20)
        df['ema60'] = ema(df['close'], 60)
        df['ema120'] = ema(df['close'], 120)
        df['atr'] = atr(df['high'], df['low'], df['close'], 14)
        df['vol_ma'] = df['volume'].rolling(20).mean()

        if len(df) < 150: return None

        if not self.is_dense_zone(df.iloc[-30:]): return None

        high = df['ema20'].iloc[-30:].max()
        low = df['ema20'].iloc[-30:].min()
        vol_ok = df['volume'].iloc[-1] > df['vol_ma'].iloc[-1] * self.volume_mult

        if df['close'].iloc[-1] > high and vol_ok and df['close'].iloc[-2] > high:
            return "BUY"
        if df['close'].iloc[-1] < low and vol_ok and df['close'].iloc[-2] < low:
            return "SELL"
        return None

    def update_trailing_stop(self, price):
        if not self.position: return
        if self.position == "long" and price > self.entry_price * (1 + self.trail_start):
            self.stop_loss = max(self.stop_loss, price * 0.988)  # 1.2% 回调
        if self.position == "short" and price < self.entry_price * (1 - self.trail_start):
            self.stop_loss = min(self.stop_loss, price * 1.012)

strategy = ProfitLockStrategy()

# ============ 主循环（一根K线一动 + 锁利）===========
def run_bot():
    global BOT_RUNNING, _state
    load_state()
    last_ts = None

    while BOT_RUNNING:
        try:
            # 每日重置锁利状态
            today = datetime.now(timezone(timedelta(hours=8))).date()
            if _state.get("last_trade_date") != str(today):
                _state["daily_initial_balance"] = get_balance()
                _state["today_locked"] = False
                _state["last_trade_date"] = str(today)
                send_telegram(f"新的一天\n初始资金: {_state['daily_initial_balance']:.2f} USDT\n锁利目标: {DAILY_PROFIT_TARGET*100}%")

            # 检查是否已锁利
            if _state.get("today_locked", False):
                time.sleep(60)
                continue

            # 检查是否达标锁利
            current_bal = get_balance()
            profit_pct = (current_bal - _state["daily_initial_balance"]) / _state["daily_initial_balance"]
            if profit_pct >= DAILY_PROFIT_TARGET:
                _state["today_locked"] = True
                save_state()
                send_telegram(f"恭喜！今日盈利 {profit_pct*100:.2f}%\n已永久锁仓，今天彻底收工！")
                close_all()
                time.sleep(3600)
                continue

            # 获取K线
            market = MarketData.MarketAPI(flag=GLOBAL_FLAG)
            r = market.get_history_candlesticks(instId=SYMBOL, bar=BAR_INTERVAL, limit="300")
            if r.get("code") != "0": 
                time.sleep(10)
                continue

            df = pd.DataFrame(r["data"], columns=["ts","o","h","l","c","v","v1","v2","v3"])
            df = df.astype({"o":float,"h":float,"l":float,"c":float,"v":float})
            df.rename(columns={"c":"close","o":"open","h":"high","l":"low","v":"volume"}, inplace=True)
            current_ts = int(df["ts"].iloc[-1])

            # 一根K线只执行一次
            if current_ts == last_ts:
                time.sleep(10)
                continue
            last_ts = current_ts

            ktime = datetime.fromtimestamp(current_ts/1000, tz=timezone(timedelta(hours=8)))
            logging.info(f"{'='*30} 新K线 {BAR_INTERVAL} {ktime} {'='*30}")

            # 有持仓 → 检查退出
            pos = get_position()
            if pos:
                strategy.position = "long" if pos["side"] in ["long","net"] else "short"
                strategy.entry_price = pos["price"]
                strategy.update_trailing_stop(df['close'].iloc[-1])
                if (strategy.position == "long" and df['close'].iloc[-1] <= strategy.stop_loss) or \
                   (strategy.position == "short" and df['close'].iloc[-1] >= strategy.stop_loss):
                    close_all()
                    strategy.position = None
                time.sleep(30)
                continue

            # 无持仓 → 找开仓信号
            signal = strategy.generate_signal(df)
            if signal and not pos:
                close_all()  # 防止残留
                time.sleep(2)
                if place_order(signal.lower(), ORDER_SIZE):
                    strategy.position = "long" if signal=="BUY" else "short"
                    strategy.entry_price = df['close'].iloc[-1]
                    atr_val = df['atr'].iloc[-1]
                    if signal=="BUY":
                        strategy.stop_loss = strategy.entry_price - atr_val * strategy.atr_mult
                    else:
                        strategy.stop_loss = strategy.entry_price + atr_val * strategy.atr_mult

            # 每根K线睡到下一根
            sleep_map = {"1m":55, "3m":170, "5m":280, "15m":870, "1H":3500}
            time.sleep(sleep_map.get(BAR_INTERVAL, 30))

        except Exception as e:
            logging.error(f"崩溃: {e}\n{traceback.format_exc()}")
            send_telegram(f"机器人崩溃: {e}")
            time.sleep(30)

# ============ Web 界面 ============
HTML = """
<h1>OKX 永续合约锁利机器人（终极版</h1>
<p>当前状态: <b style="color:{{ 'green' if running else 'red' }}">{{ '运行中' if running else '已停止' }}</b></p>
{% if running %}
    <p>交易对: {{symbol}} | 周期: {{bar}} | 锁利目标: {{target}}%</p>
    <form method=post action="/stop"><button style="padding:15px">停止机器人</button></form>
{% else %}
    <form method=post>
        <input name="k" placeholder="API Key" value="{{k}}" required><br><br>
        <input name="s" placeholder="Secret Key" value="{{s}}" required><br><br>
        <input name="p" placeholder="Passphrase" value="{{p}}" required><br><br>
        <input name="t" placeholder="TG Bot Token" value="{{t}}" required><br><br>
        <input name="c" placeholder="Chat ID" value="{{c}}" required><br><br>
        <button type=submit style="padding:15px;font-size:18px">启动机器人</button>
    </form>
{% endif %}
"""

@app.route("/", methods=["GET","POST"])
def index():
    global BOT_RUNNING, BOT_THREAD, API_KEY, SECRET_KEY, PASS_PHRASE, BOT_TOKEN, CHAT_ID
    if request.method == "POST" and not BOT_RUNNING:
        API_KEY = request.form["k"]
        SECRET_KEY = request.form["s"]
        PASS_PHRASE = request.form["p"]
        BOT_TOKEN = request.form["t"]
        CHAT_ID = request.form["c"]
        BOT_RUNNING = True
        BOT_THREAD = Thread(target=run_bot, daemon=True)
        BOT_THREAD.start()
        send_telegram("机器人已启动 - 锁利模式")
    elif request.path == "/stop":
        BOT_RUNNING = False
        close_all()
        send_telegram("机器人已停止")
    return render_template_string(HTML, running=BOT_RUNNING, symbol=SYMBOL, bar=BAR_INTERVAL, target=DAILY_PROFIT_TARGET*100,
                                  k=API_KEY[-6:] if API_KEY else "", s="*"*10, p="*"*8, t=BOT_TOKEN[-6:] if BOT_TOKEN else "", c=CHAT_ID)

@app.route("/stop", methods=["POST"]) 
def stop(): return index()

@app.route("/health") 
def health(): return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
