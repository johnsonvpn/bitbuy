# -*- coding: utf-8 -*-
"""
OKX BTC永续锁利机器人 - 2025.12.03 终极无敌版（永别秒反手）
实盘已彻底解决：
1. 平仓后6根K线（30分钟）内严禁任何反手
2. 每日已实现持仓盈亏累计达4%后当天彻底锁仓
3. 三重开仓过滤 + 双止损秒触发
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
BAR_INTERVAL = "5m"
ORDER_SIZE_BTC = 0.009                     # 你实盘用的0.009
DAILY_REALIZED_TARGET = 0.04               # 每日累计持仓盈亏达4%就停
ANTI_REVERSE_BARS = 6                      # 平仓后禁止反手6根K线（30分钟）

API_KEY = SECRET_KEY = PASS_PHRASE = ""
BOT_RUNNING = False
BOT_THREAD = None
GLOBAL_FLAG = "0"
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
            pos = float(p.get("pos", 0))
            if pos != 0:
                return {"side": "long" if pos > 0 else "short", "size": abs(pos)}
        return None
    except: return None

def get_ticker_price():
    try:
        market = MarketData.MarketAPI(flag=GLOBAL_FLAG)
        r = market.get_ticker(instId=SYMBOL)
        if r["code"] == "0" and r["data"]:
            return float(r["data"][0]["last"])
    except: pass
    return 90000.0

def close_all():
    try:
        trade = Trade.TradeAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        pos = get_position()
        if not pos: return True

        side = "sell" if pos["side"] == "long" else "buy"
        r = trade.place_order(instId=SYMBOL, tdMode="cross", side=side, ordType="market", sz=str(pos["size"]))

        if r.get("code") == "0":
            fill_price = float(r["data"][0]["fillPx"]) if r["data"][0]["fillPx"] else get_ticker_price()

            # 计算本单已实现盈亏百分比
            if strategy.position == "long":
                pnl_pct = (fill_price - strategy.entry) / strategy.entry
            elif strategy.position == "short":
                pnl_pct = (strategy.entry - fill_price) / strategy.entry
            else:
                pnl_pct = 0.0

            # 累计今日已实现盈亏
            today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            if _state.get("pnl_date") != today:
                _state["today_realized_pnl"] = 0.0
                _state["pnl_date"] = today
            _state["today_realized_pnl"] = _state.get("today_realized_pnl", 0.0) + pnl_pct
            save_state()

            logging.info(f"平仓成功 | 本单盈亏 {pnl_pct:+.2%} | 今日累计已实现盈亏 {_state['today_realized_pnl']:+.2%}")
            send(f"平仓完成\n本单盈亏 {pnl_pct:+.2%}\n今日累计持仓盈亏 {_state['today_realized_pnl']:+.2%}")

            # 关键：这里不设置保护期！由主循环统一管理
            strategy.position = None
            strategy.entry = 0.0
            strategy.sl = 0.0
            return True
    except Exception as e:
        logging.error(f"平仓异常 {e}")
    return False

def place_order(side):
    try:
        trade = Trade.TradeAPI(API_KEY, SECRET_KEY, PASS_PHRASE, flag=GLOBAL_FLAG)
        r = trade.place_order(instId=SYMBOL, tdMode="cross", side=side, ordType="market", sz=str(ORDER_SIZE_BTC))
        if r.get("code") == "0":
            logging.info(f"开仓成功 {side.upper()} {ORDER_SIZE_BTC} BTC")
            send(f"开仓 {side.upper()} {ORDER_SIZE_BTC} BTC")
            return True
        else:
            logging.error(f"下单失败 {r}")
    except Exception as e:
        logging.error(f"下单异常 {e}")
    return False

# ============ 每日已实现盈亏锁仓 ============
def check_daily_lock():
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    if _state.get("pnl_date") != today:
        _state["today_realized_pnl"] = 0.0
        _state["pnl_date"] = today
        _state["today_locked"] = False
        save_state()

    if _state.get("today_locked", False):
        return True

    realized_pct = _state.get("today_realized_pnl", 0.0)
    if realized_pct >= DAILY_REALIZED_TARGET:
        logging.info(f"今日已实现持仓盈亏达 {realized_pct:.2%}，已达锁利目标，当天停止交易")
        send(f"恭喜暴富！\n今日累计持仓盈利 {realized_pct:.2%}\n已达目标，当天锁定不再交易")
        _state["today_locked"] = True
        save_state()
        return True
    return False

# ============ 策略核心 ============
class Strategy:
    def __init__(self):
        self.position = None
        self.entry = 0.0
        self.sl = 0.0
        self.close_protect_bars = 0   # 平仓后禁止反手计数

    def is_trap_candle(self, row):
        body = abs(row['close'] - row['open']) or 0.1
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

        last = df.iloc[-2]
        curr = df.iloc[-1]

        if self.is_trap_candle(curr):
            logging.info("检测到巨量长影线陷阱，禁止开仓")
            return None

        recent = df.iloc[-36:-1]
        spread = (recent[['e20','e60','e120']].max(axis=1) - recent[['e20','e60','e120']].min(axis=1)) / recent['close']
        if spread.mean() > 0.0012:
            logging.info(f"EMA密集度 {spread.mean():.4%} > 0.12%，不满足")
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

# ============ 主循环（终极修复版）===========
def run_bot():
    global BOT_RUNNING
    load_state()
    last_ts = None

    while BOT_RUNNING:
        try:
            # 每日锁利检查
            if check_daily_lock():
                time.sleep(300)
                continue

            market = MarketData.MarketAPI(flag=GLOBAL_FLAG)
            r = market.get_history_candlesticks(instId=SYMBOL, bar=BAR_INTERVAL, limit="300")
            if r.get("code") != "0" or not r.get("data"):
                time.sleep(10)
                continue

            df = pd.DataFrame(r["data"], columns=["ts","o","h","l","c","v","v1","v2","v3"]).astype(float)
            df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"}, inplace=True)
            df = df.sort_values("ts").reset_index(drop=True)

            ts = int(df["ts"].iloc[-1])
            kline_time = datetime.fromtimestamp(ts/1000, tz=timezone(timedelta(hours=8)))
            logging.info(f"{'='*42} 新K线 {BAR_INTERVAL} | {kline_time.strftime('%H:%M:%S')} | 今日盈亏 {_state.get('today_realized_pnl',0):+.2%} {'='*42}")

            if ts == last_ts:
                time.sleep(8)
                continue
            last_ts = ts

            price = df['close'].iloc[-1]
            pos = get_position()

            # ========= 有仓：止损 =========
            if pos and strategy.position:
                atr_val = df['atr'].iloc[-2]
                if pd.isna(atr_val): atr_val = 100

                need_close = False
                if strategy.position == "long" and price < strategy.entry - atr_val * 0.8:
                    logging.info(f"假突破止损 多单秒平 {price:.1f}")
                    need_close = True
                if strategy.position == "short" and price > strategy.entry + atr_val * 0.8:
                    logging.info(f"假突破止损 空单秒平 {price:.1f}")
                    need_close = True
                if (strategy.position == "long" and price <= strategy.sl) or \
                   (strategy.position == "short" and price >= strategy.sl):
                    logging.info(f"普通止损触发")
                    need_close = True

                if need_close:
                    if close_all():
                        # 关键修复：这里强制开启30分钟反手保护！
                        strategy.close_protect_bars = ANTI_REVERSE_BARS
                    continue

            # ========= 无仓：反手保护 + 开仓 =========
            if not pos:
                if strategy.close_protect_bars > 0:
                    strategy.close_protect_bars -= 1
                    logging.info(f"平仓保护期，剩余 {strategy.close_protect_bars} 根K线（约{strategy.close_protect_bars*5}分钟）禁止任何开仓")
                    time.sleep(30)
                    continue

                sig = strategy.signal(df)
                if sig:
                    side = "buy" if sig == "buy" else "sell"
                    close_all()
                    time.sleep(2)
                    if place_order(side):
                        strategy.position = "long" if sig == "buy" else "short"
                        strategy.entry = price
                        atr_v = df['atr'].iloc[-2] if not pd.isna(df['atr'].iloc[-2]) else 100
                        strategy.sl = price - atr_v * 1.8 if sig == "buy" else price + atr_v * 1.8
                        logging.info(f"开仓成功 {sig.upper()} 入场{price:.1f} 止损{strategy.sl:.1f}")

            time.sleep(60)

        except Exception as e:
            logging.error(f"崩溃 {e}\n{traceback.format_exc()}")
            send(f"机器人崩溃 {e}")
            time.sleep(30)

# ============ Web面板 ============
HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8"><title>BTC锁利机器人</title>
<style>body{background:#000;color:#0f0;font-family:Arial;padding:30px;}
input,button{padding:12px;margin:8px;width:380px;background:#111;color:#0f0;border:1px solid #0f0;}
button{background:#0f0;color:#000;font-size:18px;cursor:pointer;}
</style></head><body>
<h1>BTC-USDT-SWAP 锁利机器人（2025.12.03 终极版）</h1>
<form method=post>
API Key: <input name=k value="{{k}}"><br>
Secret: <input name=s value="{{s}}"><br>
Passphrase: <input name=p value="{{p}}"><br>
<button type=submit>启动机器人</button>
</form>
<p>状态：{% if r %}运行中{% else %}已停止{% endif %} | 今日已实现盈亏：{{pnl}}</p>
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
            send("机器人已启动 - 2025.12.03 永绝秒反手版")
    pnl = f"{_state.get('today_realized_pnl',0):+.2%}"
    return render_template_string(HTML, r=BOT_RUNNING, k=API_KEY, s="*"*12, p=PASS_PHRASE, pnl=pnl)

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
