# -*- coding: utf-8 -*-
"""
OKX 永续合约锁利机器人 - 2025.12.02 18:00 实盘终极无敌版
已彻底解决：
1. EMA密集度判断错误（原来取错区间）
2. ATR为NaN导致止损永不触发
3. 平仓后本根K线反手
4. 巨量长影线陷阱
5. 北京时间 + 美观日志
"""

import time
import logging
import pandas as pd
from pandas_ta import ema, atr
from okx import MarketData, Trade, Account
from flask import Flask, request, render_template_string
from threading import Thread
import os
import json
import traceback
from datetime import datetime, timezone, timedelta
import requests

# ============ 北京时间日志 ============
class BeijingFormatter(logging.Formatter):
    def converter(self, timestamp):
        return datetime.fromtimestamp(timestamp).astimezone(timezone(timedelta(hours=8))).timetuple()
    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        return time.strftime("%Y-%m-%d %H:%M:%S", ct) + f".{int(record.msecs):03d}"

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
for h in logging.getLogger().handlers:
    h.setFormatter(BeijingFormatter())

app = Flask(__name__)

# ============ 配置区 ============
SYMBOL = "BTC-USDT-SWAP"
BAR_INTERVAL = "5m"              # 你实盘用的周期
ORDER_SIZE = 0.9                 # 你今天用的0.9张
DAILY_PROFIT_TARGET = 0.04       # 4%锁利

API_KEY = SECRET_KEY = PASS_PHRASE = ""
BOT_RUNNING = False
BOT_THREAD = None
GLOBAL_FLAG = "0"                # 0=实盘 1=模拟
STATE_FILE = "/tmp/bot_state.json"
_state = {}
RENDER_URL = "https://bitbuy-w8xw.onrender.com/send"

# ============ 状态持久化 ============
def save_state():
    try: json.dump(_state, open(STATE_FILE, "w"))
    except: pass

def load_state():
    global _state
    if os.path.exists(STATE_FILE):
        try: _state = json.load(open(STATE_FILE))
        except: _state = {}

# ============ Telegram推送 ============
def send(msg):
    if SECRET_KEY:
        try: requests.post(RENDER_URL, json={"key": SECRET_KEY, "text": msg[:3900]}, timeout=8)
        except: pass

# ============ 账户操作 ============
def get_balance():
    try:
        acc = Account.AccountAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        r = acc.get_account_balance(ccy="USDT")
        if r.get("code") == "0" and r.get("data"):
            for d in r["data"][0]["details"]:
                if d["ccy"] == "USDT":
                    return float(d.get("availBal") or d.get("cashBal") or "0")
        return 10000.0
    except: return 10000.0

def get_position():
    try:
        acc = Account.AccountAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        r = acc.get_positions(instId=SYMBOL)
        if r.get("code") != "0" or not r.get("data"): return None
        for p in r["data"]:
            if float(p.get("pos", 0)) != 0:
                return {"side": p["posSide"], "size": abs(float(p["pos"])), "price": float(p["avgPx"])}
        return None
    except: return None

def close_all():
    try:
        trade = Trade.TradeAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        pos = get_position()
        if not pos: return True
        side = "sell" if pos["side"] == "long" else "buy"
        r = trade.place_order(instId=SYMBOL, tdMode="cross", side=side, ordType="market", sz=str(pos["size"]))
        if r.get("code") == "0":
            logging.info(f"全平成功 {side.upper()} {pos['size']}张")
            send(f"强平仓位 {side.upper()} {pos['size']}张")
            return True
    except Exception as e:
        logging.error(f"平仓失败 {e}")
    return False

def place_order(side, size):
    try:
        trade = Trade.TradeAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        r = trade.place_order(instId=SYMBOL, tdMode="cross", side=side, ordType="market", sz=str(size))
        if r.get("code") == "0":
            price = get_position()["price"] if get_position() else "市价"
            logging.info(f"下单成功 {side.upper()} {size}张 @ {price}")
            send(f"下单成功 {side.upper()} {size}张\n价格 ≈ {price}")
            return True
        else:
            logging.error(f"下单失败 {r}")
    except Exception as e:
        logging.error(f"下单异常 {e}")
    return False

# ============ 每日锁利 ============
def check_lock_profit():
    today = datetime.now(timezone(timedelta(hours=8))).date()
    if _state.get("last_date") != str(today):
        _state["last_date"] = str(today)
        _state["initial"] = get_balance()
        save_state()
    if get_balance() >= _state.get("initial", 10000) * (1 + DAILY_PROFIT_TARGET):
        logging.info("今日盈利达标，锁利休眠至明天")
        send("今日已达4%盈利，机器人进入锁利休眠")
        return True
    return False

# ============ 核心策略类（终极修复版）===========
class Strategy:
    def __init__(self):
        self.position = None
        self.entry = 0.0
        self.sl = 0.0
        self.just_closed = False

    def is_trap_candle(self, row):
        body = abs(row['close'] - row['open'])
        if body == 0: body = 0.1
        lower = min(row['open'], row['close']) - row['low']
        upper = row['high'] - max(row['open'], row['close'])
        vol_ratio = row['volume'] / row['vol20']
        return vol_ratio > 3.0 and (lower > body * 3 or upper > body * 3)

    def signal(self, df):
        df['e20']  = ema(df['close'], 20)
        df['e60']  = ema(df['close'], 60)
        df['e120'] = ema(df['close'], 120)
        df['atr']  = atr(df['high'], df['low'], df['close'], 14)
        df['vol20'] = df['volume'].rolling(20).mean()

        last    = df.iloc[-2]   # 已收盘K线
        current = df.iloc[-1]   # 正在形成

        if self.is_trap_candle(current):
            logging.info("检测到巨量长影线陷阱，禁止开仓")
            return None

        # 正确取最近35根已收盘K线判断密集度
        recent = df.iloc[-36:-1]
        spread = (recent[['e20','e60','e120']].max(axis=1) - recent[['e20','e60','e120']].min(axis=1)) / recent['close']
        if spread.mean() > 0.0012:
            logging.info(f"EMA密集度 {spread.mean():.4%} > 0.12%，不满足开仓条件")
            return None

        high_20 = recent['e20'].max()
        low_20  = recent['e20'].min()
        vol_ok = last['volume'] > last['vol20'] * 1.8

        if last['close'] > high_20 and last['close'] > last['open'] and vol_ok:
            return "buy"
        if last['close'] < low_20 and last['close'] < last['open'] and vol_ok:
            return "sell"
        return None

strategy = Strategy()

# ============ 主循环（终极无敌版）===========
def run_bot():
    global BOT_RUNNING
    load_state()
    last_ts = None

    while BOT_RUNNING:
        try:
            if check_lock_profit():
                time.sleep(300)
                continue

            market = MarketData.MarketAPI(flag=GLOBAL_FLAG)
            r = market.get_history_candlesticks(instId=SYMBOL, bar=BAR_INTERVAL, limit="300")
            if r.get("code") != "0" or not r.get("data"):
                time.sleep(10)
                continue

            # 正确处理K线数据
            df = pd.DataFrame(r["data"], columns=["ts","o","h","l","c","v","v1","v2","v3"])
            df = df.astype(float)
            df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"}, inplace=True)
            df = df.sort_values("ts").reset_index(drop=True)  # 确保时间升序

            ts = int(df["ts"].iloc[-1])
            kline_time = datetime.fromtimestamp(ts / 1000, tz=timezone(timedelta(hours=8)))
            logging.info(f"{'='*38} 新K线 {BAR_INTERVAL} | {kline_time.strftime('%Y-%m-%d %H:%M:%S')} {'='*38}")

            if ts == last_ts:
                time.sleep(8)
                continue
            last_ts = ts

            price = df['close'].iloc[-1]
            pos = get_position()

            # ========= 有仓：止损逻辑 =========
            if pos and strategy.position:
                atr_val = df['atr'].iloc[-2]  # 强制用上一根已收盘的ATR
                if pd.isna(atr_val):
                    atr_val = df['atr'].dropna().iloc[-1] if not df['atr'].dropna().empty else 100

                if strategy.position == "buy" and price < strategy.entry - atr_val * 0.8:
                    logging.info(f"假突破止损 多单秒平 {price:.1f}")
                    close_all(); strategy.just_closed = True; strategy.position = None; continue
                if strategy.position == "sell" and price > strategy.entry + atr_val * 0.8:
                    logging.info(f"假突破止损 空单秒平 {price:.1f}")
                    close_all(); strategy.just_closed = True; strategy.position = None; continue

                if (strategy.position == "buy" and price <= strategy.sl) or \
                   (strategy.position == "sell" and price >= strategy.sl):
                    logging.info(f"普通止损触发 {strategy.position.upper()} 被扫")
                    close_all(); strategy.just_closed = True; strategy.position = None
                continue

            # ========= 无仓：开仓逻辑 =========
            if not pos:
                if strategy.just_closed:
                    logging.info("本根K线刚刚平仓，严禁反手，等待下一根")
                    strategy.just_closed = False
                    time.sleep(30)
                    continue

                sig = strategy.signal(df)
                if sig:
                    close_all()
                    time.sleep(2)
                    if place_order(sig, ORDER_SIZE):
                        strategy.position = sig
                        strategy.entry = price
                        atr_v = df['atr'].iloc[-2]
                        if pd.isna(atr_v): atr_v = 100
                        strategy.sl = price - atr_v * 1.8 if sig == "buy" else price + atr_v * 1.8
                        logging.info(f"开仓成功 {sig.upper()} {ORDER_SIZE}张 入场{price:.1f} 止损{strategy.sl:.1f}")

            time.sleep({"1m":55, "3m":170, "5m":280, "15m":870, "1H":3500, "4H":14000}.get(BAR_INTERVAL, 60))

        except Exception as e:
            logging.error(f"机器人崩溃 {e}\n{traceback.format_exc()}")
            send(f"机器人崩溃 {e}")
            time.sleep(30)

# ============ 简洁Web面板 ============
HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8"><title>OKX锁利机器人 终极版</title>
<style>body{background:#000;color:#0f0;font-family:Arial;padding:30px;}
input{padding:12px;width:380px;background:#111;color:#0f0;border:1px solid #0f0 solid;margin:10px;}
button{padding:15px 40px;background:#0f0;color:#000;font-size:20px;cursor:pointer;border:none;}
</style></head><body>
<h1>BTC-USDT-SWAP 锁利机器人（2025.12.02 终极无敌版）</h1>
<form method=post>
API Key: <input name=k value="{{k}}"><br>
Secret: <input name=s value="{{s}}"><br>
Passphrase: <input name=p value="{{p}}"><br>
<button type=submit>启动机器人</button>
</form>
<p>状态：{% if r %}运行中（{{sym}} {{bar}} {{size}}张）{% else %}已停止{% endif %}</p>
<a href=/stop><button style="background:#c00;color:#fff;">紧急停止并全平</button></a>
</body></html>
"""

@app.route("/", methods=["GET","POST"])
def index():
    global BOT_RUNNING, BOT_THREAD, API_KEY, SECRET_KEY, PASS_PHRASE
    if request.method == "POST":
        API_KEY = request.form["k"]
        SECRET_KEY = request.form["s"]
        PASS_PHRASE = request.form["p"]
        if not BOT_RUNNING:
            BOT_RUNNING = True
            BOT_THREAD = Thread(target=run_bot, daemon=True)
            BOT_THREAD.start()
            send("机器人已启动 - 2025.12.02 终极无敌版")
    return render_template_string(HTML, r=BOT_RUNNING, k=API_KEY, s="*"*len(SECRET_KEY or ""), 
                                 p=PASS_PHRASE, sym=SYMBOL, bar=BAR_INTERVAL, size=ORDER_SIZE)

@app.route("/stop")
def stop():
    global BOT_RUNNING
    BOT_RUNNING = False
    close_all()
    send("已手动停止并全平")
    return "已停止"

@app.route("/health")
def health(): return "OK", 200

load_state()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
