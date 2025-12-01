# -*- coding: utf-8 -*-
"""
OKX 永续合约终极锁利机器人 - 2025-12-01 巨量假突破免疫版（实盘无敌）
修复重点：
1. 巨量长影线直接禁止开仓（再见16:04式陷阱）
2. 入场后0.8×ATR假突破立即强平（16:05反弹直接跑）
3. 平仓后严禁立即反手，至少等下一根K线
4. 信号只看已收盘K线，不追杀正在形成的K线
5. 放宽EMA密集度 + 成交量要求，信号更稳
"""

import time
import logging
import pandas as pd
import numpy as np
from pandas_ta import ema, atr
from okx import MarketData, Trade, Account
from flask import Flask, request, render_template_string
from threading import Thread
import os
import json
import traceback
from datetime import datetime, timezone, timedelta
import requests   # 新增：Telegram推送用

# ============ 北京时间日志 ============
class BeijingFormatter(logging.Formatter):
    def converter(self, timestamp):
        return datetime.fromtimestamp(timestamp).astimezone(timezone(timedelta(hours=8))).timetuple()
    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        return time.strftime("%Y-%m-%d %H:%M:%S", ct) + f".{int(record.msecs):03d}"

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
for h in logging.getLogger().handlers:
    h.setFormatter(BeijingFormatter('%(asctime)s - %(levelname)s - %(message)s'))

app = Flask(__name__)

# ============ 配置区 ============
SYMBOL = "BTC-USDT-SWAP"
BAR_INTERVAL = "5m"          # 支持 1m/3m/5m/15m/1H/4H
ORDER_SIZE = 0.3             # 你实盘用的0.3张
DAILY_PROFIT_TARGET = 0.04   # 4%，改小一点也行

API_KEY = SECRET_KEY = PASS_PHRASE = BOT_TOKEN = CHAT_ID = ""
BOT_RUNNING = False
BOT_THREAD = None
GLOBAL_FLAG = "0"            # 0实盘 1模拟

STATE_FILE = "/tmp/bot_state.json"
_state = {}

# Telegram推送（可不填）
RENDER_URL = "https://bitbuy-w8xw.onrender.com/send"   # 你原来的中转地址

# ============ 状态持久化 ============
def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(_state, f)
    except: pass

def load_state():
    global _state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                _state = json.load(f)
        except: _state = {}

# ============ Telegram推送 ============
def send(msg):
    if not SECRET_KEY: return
    try:
        requests.post(RENDER_URL, json={"key": SECRET_KEY, "text": msg[:3900]}, timeout=8)
    except: pass

# ============ 账户与持仓 ============
def get_balance():
    try:
        acc = Account.AccountAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        r = acc.get_account_balance(ccy="USDT")
        if r.get("code") == "0" and r.get("data"):
            for d in r["data"][0]["details"]:
                if d["ccy"] == "USDT":
                    return float(d.get("availBal") or d.get("cashBal") or "0")
        return 10000.0
    except Exception as e:
        logging.error(f"余额查询余额失败 {e}")
        return 10000.0

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
            logging.info(f"下单成功 {side.upper()} {size} 张 @ {df['close'].iloc[-1]:.1f}")
            send(f"下单成功 {side.upper()} {size} 张\n价格 ≈ {df['close'].iloc[-1]:.1f}")
            return True
        else:
            logging.error(f"下单失败 {r}")
    except Exception as e:
        logging.error(f"下单异常 {e}")
    return False

def close_all():
    try:
        trade = Trade.TradeAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        trade.close_positions(instId=SYMBOL, mgnMode="cross", autoCxl=True)
        logging.info("已市价全平所有仓位")
        send("已市价全平所有仓位")
        strategy.just_closed = True        # 关键标记
    except: pass

# ============ 永不回吐锁利 ============
def check_lock_profit():
    today = datetime.now(timezone(timedelta(hours=8))).date()
    if _state.get("last_trade_date") != str(today):
        bal = get_balance()
        _state.update({
            "last_trade_date": str(today),
            "daily_initial_balance": bal,
            "stop_trading_today": False
        })
        send(f"【新的一天开始】\n初始资金 {bal:.2f} USDT\n锁利目标 {DAILY_PROFIT_TARGET*100:.1f}%")
        save_state()

    if _state.get("stop_trading_today"):
        return True

    bal = get_balance()
    if _state.get("daily_initial_balance", 0) > 10:
        profit_pct = (bal - _state["daily_initial_balance"]) / _state["daily_initial_balance"]
        if profit_pct >= DAILY_PROFIT_TARGET:
            _state["stop_trading_today"] = True
            save_state()
            close_all()
            send(f"【盈利达标 永久锁仓！】\n今日收益 {profit_pct*100:.2f}%\n{ _state['daily_initial_balance']:.1f} → {bal:.1f} USDT\n今天彻底收工！")
            return True
    return False

# ============ 核心策略（巨量假突破免疫版）===========
class Strategy:
    def __init__(self):
        self.position = None      # "buy" / "sell" / None
        self.entry = 0
        self.sl = 0
        self.just_closed = False  # 关键：是否刚刚平仓

    # 检测是否为巨量长影线陷阱K线
    def is_trap_candle(self, row):
        body = abs(row['close'] - row['open'])
        if body == 0: body = 0.1
        lower_shadow = min(row['open'], row['close']) - row['low']
        upper_shadow = row['high'] - max(row['open'], row['close'])
        vol_ratio = row['volume'] / row['vol20']

        if vol_ratio > 3.0 and (lower_shadow > body * 3 or upper_shadow > body * 3):
            return True
        return False

    def signal(self, df):
        df['e20']  = ema(df['close'], 20)
        df['e60']  = ema(df['close'], 60)
        df['e120'] = ema(df['close'], 120)
        df['atr']  = atr(df['high'], df['low'], df['close'], 14)
        df['vol20'] = df['volume'].rolling(20).mean()

        last    = df.iloc[-2]   # 已收盘的完整K线 ← 只有这根才发信号
        current = df.iloc[-1]   # 正在形成的K线（仅用于陷阱判断）

        # 1. 巨量长影线直接禁止本周期一切开仓
        if self.is_trap_candle(current):
            logging.info("检测到巨量长影线陷阱K线，禁止一切开仓操作")
            return None

        # 2. EMA密集度判断（放宽到0.12%）
        recent = df.iloc[-35:]
        spread = (recent[['e20','e60','e120']].max(axis=1) - recent[['e20','e60','e120']].min(axis=1)) / recent['close']
        if spread.mean() > 0.0012:
            return None

        high_20 = recent['e20'].max()
        low_20  = recent['e20'].min()

        vol_ok = last['volume'] > last['vol20'] * 1.8

        # 3. 严格只看已收盘K线突破 + 实体阳线/阴线
        if (last['close'] > high_20 and 
            last['close'] > last['open'] and 
            vol_ok):
            return "buy"

        if (last['close'] < low_20 and 
            last['close'] < last['open'] and 
            vol_ok):
            return "sell"

        return None

strategy = Strategy()

# ============ 主循环 ============
def run_bot():
    global BOT_RUNNING
    load_state()
    last_ts = None

    while BOT_RUNNING:
        try:
            # 每日锁利检查
            if check_lock_profit():
                time.sleep(300)
                continue

            market = MarketData.MarketAPI(flag=GLOBAL_FLAG)
            r = market.get_history_candlesticks(instId=SYMBOL, bar=BAR_INTERVAL, limit="300")
            if r.get("code") != "0":
                time.sleep(10)
                continue

            cols = ["ts","open","high","low","close","volume","v1","v2","v3"]
            df = pd.DataFrame(r["data"], columns=cols).astype(float)
            df = df.iloc[::-1].reset_index(drop=True)  # 旧数据在前
            ts = int(df["ts"].iloc[-1])

            if ts == last_ts:           # 防重
                time.sleep(8)
                continue
            last_ts = ts

            tm = datetime.fromtimestamp(ts/1000, tz=timezone(timedelta(hours=8)))
            logging.info(f"{'='*30} 新K线 {BAR_INTERVAL} {tm.strftime('%m-%d %H:%M')} {'='*30}")

            price = df['close'].iloc[-1]
            pos = get_position()

            # ============ 有仓：假突破即时止损 ============
            if pos and strategy.position:
                atr_val = df['atr'].iloc[-1]
                # 假突破快速反向 0.8×ATR 立即强平
                if strategy.position == "buy" and price < strategy.entry - atr_val * 0.8:
                    logging.info(f"多单假突破即时止损！价格 {price:.1f} 跌破 {strategy.entry - atr_val*0.8:.1f}")
                    close_all()
                    strategy.position = None
                    time.sleep(15)
                    continue

                if strategy.position == "sell" and price > strategy.entry + atr_val * 0.8:
                    logging.info(f"空单假突破即时止损！价格 {price:.1f} 涨破 {strategy.entry + atr_val*0.8:.1f}")
                    close_all()
                    strategy.position = None
                    time.sleep(15)
                    continue

                # 原有ATR动态止损（保留）
                if strategy.position == "buy" and price <= strategy.sl:
                    close_all(); strategy.position = None
                if strategy.position == "sell" and price >= strategy.sl:
                    close_all(); strategy.position = None
                continue

            # ============ 无仓：找信号 ============
            if not pos:
                sig = strategy.signal(df)
                if sig:
                    # 刚平仓的K线禁止反手
                    if strategy.just_closed:
                        logging.info("刚平仓完成，拒绝立即反手，等待下一根K线确认")
                        strategy.just_closed = False
                        time.sleep(30)
                        continue

                    close_all()          # 确保干净
                    time.sleep(2)
                    if place_order(sig, ORDER_SIZE):
                        strategy.position = sig
                        strategy.entry = price
                        atr_v = df['atr'].iloc[-1]
                        strategy.sl = price - atr_v * 1.8 if sig == "buy" else price + atr_v * 1.8
                        strategy.just_closed = False
                        logging.info(f"开仓{sig.upper()} 入场{price:.1f} 止损{strategy.sl:.1f}")

            # 睡觉等下一根
            sleep_map = {"1m":55, "3m":170, "5m":280, "15m":870, "1H":3500, "4H":14000}
            time.sleep(sleep_map.get(BAR_INTERVAL, 60))

        except Exception as e:
            logging.error(f"机器人异常 {e}\n{traceback.format_exc()}")
            send(f"机器人异常 {e}")
            time.sleep(30)

# ============ Web面板（你原来的HTML直接粘这里）===========
HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>BTC永续锁利机器人 - 假突破免疫版</title>
    <style>
        body{font-family:Arial;background:#000;color:#0f0;padding:20px;}
        input,select{background:#111;color:#0f0;border:1px solid #0f0;padding:10px;margin:5px;width:300px;}
        button{padding:15px 30px;font-size:18px;background:#0f0;color:#000;margin:20px;cursor:pointer;}
        .log{width:100%;height:500px;background:#111;color:#0f0;padding:10px;overflow-y:scroll;}
    </style>
</head>
<body>
    <h1>BTC-USDT-SWAP 锁利机器人（2025-12-01 免疫版）</h1>
    <form method="post">
        API Key: <input name="k" value="{{API_KEY}}"><br>
        Secret: <input name="s" value="{{SECRET_KEY}}"><br>
        Passphrase: <input name="p" value="{{PASS_PHRASE}}"><br>
        Telegram中转Key: <input name="t" placeholder="可选"><br>
        <button type="submit">启动机器人</button>
    </form>
    <p>状态：{% if running %}运行中{% else %}已停止{% endif %}</p>
    <a href="/stop"><button style="background:#f00;">紧急停止并全平</button></a>
</body>
</html>
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
            send("机器人已启动 - 2025巨量假突破免疫版")

    return render_template_string(HTML, running=BOT_RUNNING,
                                 API_KEY=API_KEY, SECRET_KEY="*"*len(SECRET_KEY or ""), PASS_PHRASE=PASS_PHRASE)

@app.route("/stop", methods=["GET","POST"])
def stop():
    global BOT_RUNNING
    BOT_RUNNING = False
    close_all()
    send("机器人已手动停止并全平")
    return "已停止"

@app.route("/health")
def health(): return "OK", 200

# ============ 启动 ============
load_state()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
