import time
import requests
import logging
import pandas as pd
import numpy as np
from okx import MarketData, Trade, Account
from flask import Flask, request, render_template_string, json
from threading import Thread
import os
import re
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
        if datefmt:
            return time.strftime(datefmt, ct)
        else:
            return time.strftime("%Y-%m-%d %H:%M:%S", ct) + f".{int(record.msecs):03d}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()]
)
for handler in logging.getLogger().handlers:
    handler.setFormatter(BeijingFormatter())

# ============ 配置 ============
IS_DEMO = False
DEFAULT_SYMBOL = "BTC-USDT-SWAP"
DEFAULT_BAR_INTERVAL = "1m"
DEFAULT_ORDER_SIZE = 0.01
RENDER_URL = "https://bitbuy-w8xw.onrender.com/send"
CONFIG_FILE = "/tmp/config_history.json"
STATE_FILE = "/tmp/bot_state.json"

app = Flask(__name__)

# 全局变量
SYMBOL = DEFAULT_SYMBOL
BAR_INTERVAL = DEFAULT_BAR_INTERVAL
ORDER_SIZE = DEFAULT_ORDER_SIZE
API_KEY = SECRET_KEY = PASS_PHRASE = BOT_TOKEN = CHAT_ID = ""
USER_STRATEGY_CODE = ""
CONVERTED_STRATEGY_CODE = ""
BOT_RUNNING = False
BOT_THREAD = None
GLOBAL_FLAG = "0"
_state = {}

# ============ 配置历史 ============
def load_config_history():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        return []
    except:
        return []

def save_config_history(api_key, secret_key, pass_phrase, bot_token, chat_id):
    configs = load_config_history()
    new_config = {
        "api_key": api_key[-4:] if api_key else "",
        "secret_key": secret_key[-4:] if secret_key else "",
        "pass_phrase": pass_phrase[-4:] if pass_phrase else "",
        "bot_token": bot_token[-4:] if bot_token else "",
        "chat_id": chat_id,
        "full_config": {"api_key": api_key, "secret_key": secret_key, "pass_phrase": pass_phrase, "bot_token": bot_token, "chat_id": chat_id}
    }
    configs = [c for c in configs if c["full_config"] != new_config["full_config"]]
    configs.append(new_config)
    configs = configs[-5:]
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(configs, f)
    except Exception as e:
        logging.warning(f"保存配置失败: {e}")

# ============ 状态持久化 ============
def save_bot_state():
    state = {
        "running": BOT_RUNNING,
        "symbol": SYMBOL,
        "bar_interval": BAR_INTERVAL,
        "order_size": ORDER_SIZE,
        "is_demo": IS_DEMO,
        "api_key_last4": API_KEY[-4:] if API_KEY else "",
        "secret_key_last4": SECRET_KEY[-4:] if SECRET_KEY else "",
        "pass_phrase_last4": PASS_PHRASE[-4:] if PASS_PHRASE else "",
        "bot_token_last4": BOT_TOKEN[-4:] if BOT_TOKEN else "",
        "chat_id": CHAT_ID,
        "user_strategy_code": USER_STRATEGY_CODE,
        "converted_strategy_code": CONVERTED_STRATEGY_CODE,
        "last_trade_date": _state.get("last_trade_date").isoformat() if _state.get("last_trade_date") else None,
        "daily_initial_balance": _state.get("daily_initial_balance", 0),
        "profit_target": _state.get("profit_target", 0),
        "stop_trading_today": _state.get("stop_trading_today", False)
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
        logging.info("状态已保存")
    except Exception as e:
        logging.warning(f"保存运行状态失败: {e}")

def load_bot_state():
    global BOT_RUNNING, SYMBOL, BAR_INTERVAL, ORDER_SIZE, IS_DEMO, GLOBAL_FLAG
    global API_KEY, SECRET_KEY, PASS_PHRASE, BOT_TOKEN, CHAT_ID, USER_STRATEGY_CODE, CONVERTED_STRATEGY_CODE
    global _state

    if not os.path.exists(STATE_FILE):
        return False

    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)

        if not state.get("running", False):
            return False

        BOT_RUNNING = True
        SYMBOL = state["symbol"]
        BAR_INTERVAL = state["bar_interval"]
        ORDER_SIZE = state["order_size"]
        IS_DEMO = state["is_demo"]
        GLOBAL_FLAG = "1" if IS_DEMO else "0"

        API_KEY = "****" + state["api_key_last4"] if state.get("api_key_last4") else ""
        SECRET_KEY = "****" + state["secret_key_last4"] if state.get("secret_key_last4") else ""
        PASS_PHRASE = "****" + state["pass_phrase_last4"] if state.get("pass_phrase_last4") else ""
        BOT_TOKEN = "****" + state["bot_token_last4"] if state.get("bot_token_last4") else ""
        CHAT_ID = state["chat_id"]
        USER_STRATEGY_CODE = state.get("user_strategy_code", "")
        CONVERTED_STRATEGY_CODE = state.get("converted_strategy_code", "")

        _state = {
            "last_trade_date": datetime.fromisoformat(state["last_trade_date"]).date() if state.get("last_trade_date") else None,
            "daily_initial_balance": state.get("daily_initial_balance", 0),
            "profit_target": state.get("profit_target", 0),
            "stop_trading_today": state.get("stop_trading_today", False)
        }

        logging.info("恢复机器人运行状态")
        return True
    except Exception as e:
        logging.warning(f"加载运行状态失败: {e}")
        try:
            os.remove(STATE_FILE)
        except:
            pass
    return False

# ============ 非阻塞通知 ============
def send_telegram_message(message: str):
    if not SECRET_KEY or not message.strip():
        return
    data = {"key": SECRET_KEY, "text": message[:4000]}
    def _send():
        try:
            requests.post(RENDER_URL, json=data, timeout=5)
        except:
            pass
    Thread(target=_send, daemon=True).start()

# ============ 获取账户余额（根据 V5 文档优化） ============
def get_account_balance():
    flag = GLOBAL_FLAG
    try:
        acc = Account.AccountAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        # 根据文档：使用 ccy="USDT" 精确查询，减少 payload
        balance = acc.get_account_balance(ccy="USDT")
        if balance.get("code") == "0" and balance.get("data") and balance["data"]:
            details = balance["data"][0].get("details", [])
            if details:
                # 遍历 details 查找 USDT（文档：details 是币种数组）
                usdt_detail = next((item for item in details if item.get("ccy") == "USDT"), None)
                if usdt_detail:
                    # 优先返回 availBal（可用余额），fallback 到 cashBal（总余额）
                    cash_bal = usdt_detail.get("availBal", usdt_detail.get("cashBal", "0"))
                    return float(cash_bal)
        # 错误日志：添加 code/msg 便于调试（文档常见错误：60001/60004）
        err_msg = balance.get("msg", "未知错误")
        err_code = balance.get("code", "未知")
        logging.warning(f"获取余额失败 (code: {err_code}): {err_msg}")
        send_telegram_message(f"余额查询失败 (code: {err_code}): {err_msg} - 请检查 API 权限（需 'read_only' 或 'trade'）")
    except Exception as e:
        logging.error(f"获取余额异常: {e}")
        send_telegram_message(f"余额查询异常: {e}")
    return None

# ============ OKX 函数（无 timeout） ============
def get_latest_price_and_indicators(symbol: str, bar: str, max_retries=5):
    flag = GLOBAL_FLAG
    for attempt in range(max_retries):
        try:
            market = MarketData.MarketAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
            ticker = market.get_ticker(instId=symbol)
            if ticker.get("code") != "0":
                logging.warning(f"API 响应错误 (尝试 {attempt+1}): {ticker.get('msg', '未知')}")
                time.sleep(2 ** attempt)
                continue
            price = float(ticker["data"][0]["last"])

            hist = market.get_history_candlesticks(instId=symbol, bar=bar, limit="300")
            if hist.get("code") != "0" or not hist.get("data"):
                logging.warning(f"K线数据错误 (尝试 {attempt+1}): {hist.get('msg', '无数据')}")
                time.sleep(2 ** attempt)
                continue

            candles = hist["data"]
            logging.info(f"数据获取成功: 价格={price}, K线数={len(candles)}")
            return {"price": price, "candles": candles}

        except Exception as e:
            logging.error(f"获取数据异常 (尝试 {attempt+1}): {e}")
        
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt + np.random.uniform(0, 1)
            logging.info(f"重试等待 {wait_time:.1f}s...")
            time.sleep(wait_time)
    
    send_telegram_message("警告: 网络异常: 无法连接 OKX API，已重试 5 次。")
    logging.error("所有重试失败")
    return None

# ============ 下单 & 平仓 ============
def place_order(side: str, price: float, size: float):
    flag = GLOBAL_FLAG
    try:
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        pos_side = "long" if side == "buy" else "short"
        sz = str(size)
        order = trade.place_order(instId=SYMBOL, tdMode="cross", side=side, posSide=pos_side, ordType="market", sz=sz)
        if order.get("code") == "0" and order.get("data") and order["data"][0].get("sCode") == "0":
            beijing_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
            print("\n" + "="*60)
            print(f"下单成功 {side.upper()} 成功".center(60))
            print(f"数量: {size} | 价格: {price:.2f} | 时间: {beijing_time}")
            print("="*60 + "\n")
            tg_msg = f"<b>下单成功 {side.upper()} 成功</b>\n数量: <code>{size}</code>\n价格: <code>{price:.2f}</code>"
            send_telegram_message(tg_msg)
            return True
        else:
            err = order.get("data", [{}])[0].get("sMsg", "") or order.get("msg", "未知")
            print(f"下单失败: {err}")
            send_telegram_message(f"下单失败: {err}")
    except Exception as e:
        send_telegram_message(f"下单异常: {e}")
    return False

def close_position():
    flag = GLOBAL_FLAG
    try:
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        acc = Account.AccountAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        for _ in range(3):
            positions = acc.get_positions(instId=SYMBOL)
            if positions.get("code") != "0" or not positions.get("data"):
                return True
            for pos in positions["data"]:
                pos_side = pos.get("posSide")
                if pos_side in ["long", "short"]:
                    r = trade.close_positions(instId=SYMBOL, mgnMode="cross", posSide=pos_side, autoCxl=False)
                    if r.get("code") == "0":
                        send_telegram_message(f"平仓成功: {pos_side}")
            time.sleep(2)
        send_telegram_message("平仓超时")
        return False
    except Exception as e:
        send_telegram_message(f"平仓异常: {e}")
        return False

# ============ Pine → Python 转换器 ============
def convert_pine_to_python(pine_code: str) -> str:
    code = pine_code.strip()
    if not code:
        return ""

    params = {}
    clean_lines = []
    for line in code.split('\n'):
        line = line.strip()
        if not line or line.startswith('//'):
            clean_lines.append('')
            continue

        match = re.search(r'(\w+)\s*=\s*input\.[^(]*\(([^,)]+)', line)
        if match:
            name, default = match.groups()
            default = default.strip()
            try:
                val = float(default) if '.' in default else int(default)
            except:
                val = {"true": True, "false": False}.get(default.lower(), default.strip('"\''))
            params[name] = val
            clean_lines.append('')
            continue

        clean_lines.append(line)

    code = '\n'.join(clean_lines)
    code = re.sub(r'plot\([^)]*\)', '', code)
    code = re.sub(r'plotshape\([^)]*\)', '', code)
    code = re.sub(r'fill\([^)]*\)', '', code)
    code = re.sub(r'alertcondition\([^)]*\)', '', code)
    code = re.sub(r'input\([^)]*\)', '', code)

    code = re.sub(r'\bhl2\b', r'(df["high"] + df["low"]) / 2', code)
    code = re.sub(r'\bclose\[1\]\b', r'df["close"].iloc[-2] if len(df) > 1 else df["close"].iloc[-1]', code)
    code = re.sub(r'\bhigh\[1\]\b', r'df["high"].iloc[-2] if len(df) > 1 else df["high"].iloc[-1]', code)
    code = re.sub(r'\blow\[1\]\b', r'df["low"].iloc[-2] if len(df) > 1 else df["low"].iloc[-1]', code)

    code = re.sub(r'sma\(tr,\s*(\w+)\)', r'df["tr"].rolling(window=\1, min_periods=\1).mean().iloc[-1]', code)
    code = re.sub(r'atr\((\w+)\)', r'df["tr"].rolling(window=\1, min_periods=\1).mean().iloc[-1]', code)

    code = re.sub(r'(\w+)\s*:=', r'_state["\1"] =', code)
    code = re.sub(r'var\s+\w+\s+(\w+)\s*=\s*na', r'_state["\1"] = None', code)
    code = re.sub(r'var\s+\w+\s+(\w+)\s*=\s*([\d.]+)', r'_state["\1"] = \2', code)
    code = re.sub(r'nz\((\w+)\[1\],\s*\1\)', r'_state.get("\1", \1)', code)

    code = re.sub(r'([^?]+)\?\s*([^:]+)\s*:\s*(.+)', r'(\2) if (\1) else (\3)', code)

    python_code = f'''
# 自动转换自 Pine Script
import pandas as pd
PERIODS = {params.get("Periods", 10)}
MULTIPLIER = {params.get("Multiplier", 3.0)}
_state = {{"up": None, "dn": None, "trend": 1, "initialized": False}}
def generate_signal(data):
    global _state
    candles = data["candles"]
    if len(candles) < 15:
        return None
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"]).astype(float)
    df = df.iloc[::-1].reset_index(drop=True)
    df["tr0"] = abs(df["high"] - df["low"])
    df["tr1"] = abs(df["high"] - df["close"].shift(1))
    df["tr2"] = abs(df["low"] - df["close"].shift(1))
    df["tr"] = df[["tr0", "tr1", "tr2"]].max(axis=1)
    atr = df["tr"].rolling(window=PERIODS, min_periods=PERIODS).mean().iloc[-1]
    if pd.isna(atr):
        return None
    latest_idx = len(df) - 1
    src = (df["high"] + df["low"]) / 2
    up = src.iloc[latest_idx] - MULTIPLIER * atr
    dn = src.iloc[latest_idx] + MULTIPLIER * atr
    close_curr = df["close"].iloc[latest_idx]
    close_prev = df["close"].iloc[latest_idx - 1] if latest_idx > 0 else close_curr
    up_prev = _state["up"] if _state["initialized"] else up
    dn_prev = _state["dn"] if _state["initialized"] else dn
    if close_prev > up_prev:
        up = max(up, up_prev)
    if close_prev < dn_prev:
        dn = min(dn, dn_prev)
    trend = _state["trend"]
    if trend == -1 and close_curr > dn_prev:
        trend = 1
    elif trend == 1 and close_curr < up_prev:
        trend = -1
    prev_trend = _state["trend"]
    buy_signal = trend == 1 and prev_trend == -1
    sell_signal = trend == -1 and prev_trend == 1
    _state.update({{"up": up, "dn": dn, "trend": trend, "initialized": True}})
    if buy_signal: return "buy"
    if sell_signal: return "sell"
    return None
'''.strip()

    return python_code

# ============ 智能策略转换 ============
def convert_strategy_code(raw_code: str) -> str:
    raw_code = raw_code.strip()
    if not raw_code:
        return ""

    pine_keywords = ["input(", "plot(", "strategy(", "study(", "=>", "hline(", "ta.", "var ", "alertcondition("]
    is_pine = any(kw in raw_code for kw in pine_keywords)

    if is_pine:
        logging.info("检测到 Pine Script，正在生成 Python 策略...")
        try:
            return convert_pine_to_python(raw_code)
        except Exception as e:
            raise ValueError(f"转换失败: {e}")

    match = re.search(r'def\s+generate_signal\s*\([^)]*\)\s*:\s*(.*)', raw_code, re.DOTALL | re.MULTILINE)
    if not match:
        raise ValueError("Python 策略必须包含 `def generate_signal(data):` 函数")

    user_body = match.group(1).strip()

    enhanced_template = f'''
import pandas as pd
from datetime import datetime, timezone, timedelta
BEIJING_TZ = timezone(timedelta(hours=8))
def generate_signal(data):
    candles = data["candles"]
    if len(candles) < 10:
        return None
    df = pd.DataFrame(candles, columns=["ts","open","high","low","close","volume","volCcy","volCcyQuote","confirm"]).astype(float)
    df = df.iloc[::-1].reset_index(drop=True)
    latest_ts = df["ts"].iloc[-1] / 1000
    latest_dt = datetime.fromtimestamp(latest_ts, tz=BEIJING_TZ)
    today = latest_dt.date()
    signal = None
    try:
        {user_body}
    except Exception as e:
        raise RuntimeError(f"用户策略代码错误: {{e}}") from e
    if signal in ["buy", "sell"]:
        return signal
    return None
'''.strip()

    return enhanced_template

# ============ 机器人主循环（严格按面板K线周期执行，一根K线只交易一次）===========
def run_bot():
    global BOT_RUNNING, CONVERTED_STRategy_CODE, GLOBAL_FLAG, _state

    mode = "模拟盘" if IS_DEMO else "实盘"
    logging.info("机器人启动")
    send_telegram_message(f"策略启动 | {mode} | {SYMBOL} | {BAR_INTERVAL} | 金额: {ORDER_SIZE}")

    # 编译策略
    ns = {}
    try:
        exec(CONVERTED_STRATEGY_CODE, ns)
        generate_signal = ns.get("generate_signal")
        if not generate_signal: raise ValueError("未找到 generate_signal 函数")
        logging.info("策略编译成功")
    except Exception as e:
        logging.error(f"策略编译失败: {e}")
        send_telegram_message(f"策略编译失败: {e}")
        return

    # 持久化状态（含今日初始余额、目标、是否已收工）
    _state = _state or {
        "last_trade_date": None,
        "daily_initial_balance": 0.0,
        "profit_target_pct": 0.0,      # 今日目标 3~5%
        "stop_trading_today": False    # 达标后永久锁仓
    }

    last_signal = None
    last_processed_ts = None
# ===== 全面接口测试模式（启动即测试所有关键功能）=====
    TEST_DIRECT_ORDER = False # 开启全面测试模式
    TEST_SIDE = "sell"                # 测试下单方向（建议先用 sell，避免意外开多）

    if TEST_DIRECT_ORDER:
        logging.info("【全面接口测试模式】开始执行全链路测试...")
        send_telegram_message("【OKX 接口全面测试开始】\n正在逐项验证关键功能...")

        test_results = {
            "时间同步": "成功",
            "获取行情": "失败",
            "获取余额": "失败",
            "下单功能": "失败",
            "持仓查询": "失败",
            "强制平仓": "失败（无持仓也算成功）",
            "Telegram通知": "成功"  # 这一条肯定能发出来
        }

        all_pass = True

        try:
            # 1. 时间同步（北京时间）
            beijing_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
            test_results["时间同步"] = "成功 " + beijing_time

            # 2. 获取最新价格和K线
            data = get_latest_price_and_indicators(SYMBOL, BAR_INTERVAL, max_retries=5)
            if data and data.get("price") and data.get("candles"):
                price = data["price"]
                test_results["获取行情"] = f"成功 最新价: {price:.2f}"
                logging.info(f"行情获取成功: {price}")
            else:
                all_pass = False
                logging.error("行情获取失败")

            # 3. 获取账户余额
            balance = get_account_balance()
            if balance is not None and balance > 0:
                test_results["获取余额"] = f"成功 余额: {balance:.2f} USDT"
                logging.info(f"余额查询成功: {balance}")
            elif balance == 0:
                test_results["获取余额"] = "成功 余额为 0（模拟盘正常）"
            else:
                all_pass = False
                test_results["获取余额"] = "失败 无法读取"
                logging.error("余额查询失败")

            # 4. 下单测试（市价单）
            if data:
                send_telegram_message(f"正在测试下单功能：{TEST_SIDE.upper()} {ORDER_SIZE} 张")
                success = place_order(TEST_SIDE, price, ORDER_SIZE)
                if success:
                    test_results["下单功能"] = "成功 已下单"
                    logging.info("下单测试成功")
                else:
                    all_pass = False
                    test_results["下单功能"] = "失败 请检查权限/资金"
            else:
                all_pass = False
                test_results["下单功能"] = "跳过（无行情）"

            time.sleep(4)  # 等待订单成交

            # 5. 查询持仓
            try:
                acc = Account.AccountAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=GLOBAL_FLAG)
                positions = acc.get_positions(instId=SYMBOL)
                if positions.get("code") == "0":
                    pos_list = positions["data"]
                    if pos_list:
                        pos = pos_list[0]
                        side = pos.get("posSide")
                        size = float(pos.get("pos", 0))
                        test_results["持仓查询"] = f"成功 当前持仓: {side} {size} 张"
                    else:
                        test_results["持仓查询"] = "成功 无持仓（刚平或未成交）"
                else:
                    test_results["持仓查询"] = "失败 API错误"
                    all_pass = False
            except Exception as e:
                test_results["持仓查询"] = f"异常 {str(e)[:30]}"
                all_pass = False

            # 6. 强制平仓测试
            send_telegram_message("正在测试强制平仓功能...")
            close_success = close_position()
            if close_success or "无持仓" in str(close_success):
                test_results["强制平仓"] = "成功 已清仓或本来就空"
            else:
                test_results["强制平仓"] = "失败 请手动检查"
                all_pass = False

            # ===== 最终报告 =====
            report_lines = ["【OKX 接口测试完成】\n"]
            for name, result in test_results.items():
                icon = "成功" if "成功" in result else "失败"
                report_lines.append(f"{icon} {name}: {result}")

            summary = "\n\n【全部正常，可正式运行策略！】" if all_pass else "\n\n【存在问题，请先修复后再实盘！】"
            report_lines.append(summary)

            final_message = "\n".join(report_lines)
            send_telegram_message(final_message)
            logging.info(final_message)

            if all_pass:
                send_telegram_message("测试通过！所有核心接口正常")
            else:
                send_telegram_message("测试未全部通过，建议先用模拟盘验证")

        except Exception as critical_error:
            error_detail = f"测试过程崩溃: {critical_error}\n{traceback.format_exc()}"
            logging.error(error_detail)
            send_telegram_message(f"【测试失败】程序异常崩溃！\n{str(critical_error)}")
        
        # 测试完毕，主动退出程序
        logging.info("接口测试结束，程序即将退出")
        send_telegram_message("接口测试已完成，程序即将停止运行")
        time.sleep(3)
        os._exit(0)  # 强制退出，防止继续跑策略

    while BOT_RUNNING:
        try:
            # 拉最新K线
            data = get_latest_price_and_indicators(SYMBOL, BAR_INTERVAL, max_retries=5)
            if not data:
                time.sleep(10)
                continue

            current_bar_ts = int(data["candles"][-1][0])
            if current_bar_ts == last_processed_ts:
                time.sleep(3)
                continue

            # 新K线触发！
            last_processed_ts = current_bar_ts
            kline_time = datetime.fromtimestamp(current_bar_ts / 1000, tz=timezone(timedelta(hours=8)))
            logging.info(f"{'='*25} 新K线 {BAR_INTERVAL} | {kline_time.strftime('%Y-%m-%d %H:%M:%S')} {'='*25}")

            # 新的一天：初始化盈利目标
            today = kline_time.date()
            if _state["last_trade_date"] != today:
                initial_bal = get_account_balance() or 10000.0
                target_pct = round(np.random.uniform(3.0, 5.0), 2)

                _state.update({
                    "last_trade_date": today,
                    "daily_initial_balance": initial_bal,
                    "profit_target_pct": target_pct,
                    "stop_trading_today": False
                })
                send_telegram_message(
                    f"新的一天开始\n"
                    f"初始资金: {initial_bal:.2f} USDT\n"
                    f"今日盈利目标: <b>{target_pct}%</b>\n"
                    f"达标后立即锁仓，全天不再交易"
                )

            # 核心：已达标就彻底躺平
            if _state["stop_trading_today"]:
                logging.info("今日已达标，锁仓中，忽略所有信号")
                wait_seconds = {"1m":55,"3m":150,"5m":250,"15m":850,"1H":3500,"4H":14000}.get(BAR_INTERVAL, 30)
                time.sleep(wait_seconds)
                continue

            # 执行策略
            signal = generate_signal(data)

            if signal and signal != last_signal:
                # 先平当前仓位（无论盈亏都平）
                close_position()
                time.sleep(3)

                # 关键：平仓后立即检查是否已达标
                current_balance = get_account_balance()
                if current_balance and _state["daily_initial_balance"] > 0:
                    profit_pct = (current_balance - _state["daily_initial_balance"]) / _state["daily_initial_balance"] * 100
                    profit_pct = round(profit_pct, 2)

                    if profit_pct >= _state["profit_target_pct"]:
                        _state["stop_trading_today"] = True
                        send_telegram_message(
                            f"恭喜！今日盈利 <b>{profit_pct}%</b> ≥ 目标 {_state['profit_target_pct']}%\n"
                            f"已永久锁仓，今天不再开任何单子\n"
                            f"躺平享受胜利果实"
                        )
                        logging.info(f"达标锁仓！盈利 {profit_pct}%")
                        # 达标后直接跳过开仓逻辑
                        last_signal = signal
                        wait_seconds = {"1m":55,"3m":150,"5m":250,"15m":850,"1H":3500,"4H":14000}.get(BAR_INTERVAL, 30)
                        time.sleep(wait_seconds)
                        continue

                    else:
                        # 未达标，正常开新仓
                        if place_order(signal, data["price"], ORDER_SIZE):
                            send_telegram_message(
                                f"开{signal.upper()}仓\n"
                                f"当前盈利率: {profit_pct}%\n"
                                f"目标: {_state['profit_target_pct']}%"
                            )
                        last_signal = signal
                else:
                    # 余额获取失败也继续开（不影响主逻辑）
                    place_order(signal, data["price"], ORDER_SIZE)
                    last_signal = signal

            # 本根K线处理完毕，长睡到下一根
            wait_map = {"1m":55,"3m":150,"5m":250,"15m":850,"1H":3500,"4H":14000,"1D":80000}
            wait_seconds = wait_map.get(BAR_INTERVAL, 30)
            logging.info(f"本K线处理完成，等待下一根（约 {wait_seconds}s）")
            time.sleep(wait_seconds)

        except Exception as e:
            logging.error(f"机器人崩溃: {e}\n{traceback.format_exc()}")
            send_telegram_message(f"机器人异常停止: {e}")
            BOT_RUNNING = False
            close_position()
            if os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)
            break

# ============ HTML 模板（无全角） ============
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>OKX 策略启动器(实盘/模拟盘)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{font-family:Arial;margin:40px;background:#f4f4f4}
        .c{max-width:1000px;margin:auto;background:#fff;padding:30px;border-radius:10px;box-shadow:0 0 10px rgba(0,0,0,.1)}
        input,select,textarea{width:100%;padding:12px;margin:8px 0;border:1px solid #ccc;border-radius:6px;font-size:15px}
        button{background:#28a745;color:#fff;padding:15px;border:none;border-radius:6px;cursor:pointer;font-size:18px;font-weight:bold;width:100%;margin:10px 0;position:relative}
        .cancel-btn{background:#dc3545}
        button:hover{background:#218838}
        .cancel-btn:hover{background:#c82333}
        .s{color:#28a745;font-weight:bold}
        .e{color:#dc3545;font-weight:bold}
        .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:15px;margin:20px 0}
        .box{background:#fff;padding:15px;border-radius:6px;text-align:center;border:1px solid #ddd}
        .config-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}
        .loading{position:absolute;top:0;left:0;width:100%;height:100%;background:rgba(255,255,255,0.9);border-radius:6px;display:flex;align-items:center;justify-content:center;flex-direction:column;font-weight:bold;color:#007bff;z-index:10}
        .spinner{border:4px solid #f3f3f3;border-top:4px solid #007bff;border-radius:50%;width:30px;height:30px;animation:spin 1s linear infinite;margin-bottom:10px}
        @keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
        .tip{font-size:13px;color:#28a745;margin-top:5px}
    </style>
</head>
<body>
<div class="c">
    <h1>OKX 策略启动器(实盘 / 模拟盘)</h1>
    {% if error %}
        <p class="e">{{ error }}</p>
    {% endif %}
    {% if success %}
        <p class="s">策略运行中!</p>
        <div class="grid">
            <div class="box"><h3>交易对</h3><p>{{ symbol }}</p></div>
            <div class="box"><h3>K线周期</h3><p>{{ bar }}</p></div>
            <div class="box"><h3>金额</h3><p>{{ order_size }}</p></div>
            <div class="box"><h3>交易模式</h3><p>{{ mode }}</p></div>
        </div>
        <form method="post" action="/cancel" style="position:relative" onsubmit="return showLoading(this, '取消中...')">
            <button type="submit" class="cancel-btn" id="cancelBtn">取消策略</button>
            <div class="loading" id="cancelLoading" style="display:none">
                <div class="spinner"></div>
                <div>取消中...</div>
            </div>
        </form>
    {% else %}
    <form method="post" style="position:relative" onsubmit="return showLoading(this, '启动中...')">
        <div class="config-grid">
            <div><label><strong>OKX API Key</strong></label><input name="api_key" placeholder="输入 API Key" value="{{ api_key or '' }}"></div>
            <div><label><strong>OKX Secret Key</strong></label><input name="secret_key" placeholder="输入 Secret Key" value="{{ secret_key or '' }}"></div>
            <div><label><strong>OKX Passphrase</strong></label><input name="pass_phrase" placeholder="输入 Passphrase" value="{{ pass_phrase or '' }}"></div>
            <div><label><strong>Telegram Bot Token</strong></label><input name="bot_token" placeholder="输入 Bot Token" value="{{ bot_token or '' }}"></div>
            <div><label><strong>Telegram Chat ID</strong></label><input name="chat_id" placeholder="输入 Chat ID" value="{{ chat_id or '' }}"></div>
            <div>
                <label><strong>历史配置(推荐快速恢复)</strong></label>
                <select name="config_index" onchange="if(this.value!==''){let c=configs[this.value];this.form.api_key.value=c.api_key;this.form.secret_key.value=c.secret_key;this.form.pass_phrase.value=c.pass_phrase;this.form.bot_token.value=c.bot_token;this.form.chat_id.value=c.chat_id;}">
                    <option value="">选择历史配置(上次)</option>
                    {% for i, config in configs %}
                    <option value="{{ i }}" {% if i == last_config_index %}selected{% endif %}>API: {{ config.api_key }} | Bot: {{ config.bot_token }}</option>
                    {% endfor %}
                </select>
                <script>var configs = {{ configs_json | safe }};</script>
            </div>
        </div>
        <label><strong>交易对</strong></label>
        <input name="symbol" placeholder="BTC-USDT-SWAP" value="{{ symbol or '' }}">
        <label><strong>K线周期</strong></label>
        <select name="bar">
            <option value="1m" {% if bar == "1m" %}selected{% endif %}>1 分钟</option>
            <option value="3m" {% if bar == "3m" %}selected{% endif %}>3 分钟</option>
            <option value="5m" {% if bar == "5m" %}selected{% endif %}>5 分钟</option>
            <option value="15m" {% if bar == "15m" %}selected{% endif %}>15 分钟</option>
            <option value="1H" {% if bar == "1H" %}selected{% endif %}>1 小时</option>
        </select>
        <label><strong>下单金额</strong></label>
        <input name="order_size" type="number" step="0.001" placeholder="0.01" value="{{ order_size or '0.01' }}">
        <label><strong>交易模式</strong></label>
        <select name="trade_mode">
            <option value="real" {% if not demo %}selected{% endif %}>实盘交易</option>
            <option value="demo" {% if demo %}selected{% endif %}>模拟盘(模拟)</option>
        </select>
        <label><strong>策略代码(粘贴 Pine Script 或 Python)</strong></label>
        <textarea name="strategy_code" rows="15" placeholder="//@version=5\nindicator(...)">{{ default_code }}</textarea>
        <p class="tip">
            支持:<strong>Pine Script</strong>(自动转换)<br>
            支持:<strong>Python</strong>(直接运行)<br>
            信号:返回 <code>"buy"</code> 或 <code>"sell"</code><br>
            限额:代码顶部加 <code>// MAX_TRADES_PER_DAY = 3</code>
        </p>
        <div style="position:relative">
            <button type="submit" id="startBtn">启动策略</button>
            <div class="loading" id="startLoading" style="display:none">
                <div class="spinner"></div>
                <div>启动中...</div>
            </div>
        </div>
    </form>
    {% endif %}
</div>
<script>
function showLoading(form, text) {
    const btn = form.querySelector('button[type="submit"]');
    const loadingId = btn.id === 'startBtn' ? 'startLoading' : 'cancelLoading';
    const loading = document.getElementById(loadingId);
    if (!loading) return true;
    btn.disabled = true;
    loading.style.display = 'flex';
    loading.querySelector('div:last-child').textContent = text;
    return true;
}
</script>
</body>
</html>
'''

# ============ 默认 Pine 示例 ============
DEFAULT_PINE_EXAMPLE = '''
//@version=5
indicator("SuperTrend", overlay=true)
Periods = input(10)
Multiplier = input(3.0)
atr = ta.atr(Periods)
up = hl2 - Multiplier * atr
dn = hl2 + Multiplier * atr
var float trend = 1
var float up_prev = na
var float dn_prev = na
up := close[1] > up_prev ? max(up, up_prev) : up
dn := close[1] < dn_prev ? min(dn, dn_prev) : dn
trend := close > dn_prev ? 1 : close < up_prev ? -1 : trend
up_prev := up
dn_prev := dn
'''

# ============ Flask 路由 ============
@app.route('/', methods=['GET', 'POST'])
def index():
    global SYMBOL, BAR_INTERVAL, ORDER_SIZE, CONVERTED_STRATEGY_CODE, BOT_RUNNING, BOT_THREAD
    global API_KEY, SECRET_KEY, PASS_PHRASE, BOT_TOKEN, CHAT_ID, USER_STRATEGY_CODE, IS_DEMO, GLOBAL_FLAG

    configs = [(i, c) for i, c in enumerate(load_config_history())]
    configs_json = [c["full_config"] for c in load_config_history()]
    last_config_index = configs[-1][0] if configs else -1

    is_running = load_bot_state()

    if request.method == 'POST':
        symbol = request.form.get('symbol', '').strip() or DEFAULT_SYMBOL
        bar = request.form.get('bar', '1m').strip()
        order_size_str = request.form.get('order_size', '0.01').strip()
        trade_mode = request.form.get('trade_mode', 'real').strip()
        IS_DEMO = (trade_mode == 'demo')
        GLOBAL_FLAG = "1" if IS_DEMO else "0"
        strategy_code = request.form.get('strategy_code', '').strip()
        api_key = request.form.get('api_key', '').strip()
        secret_key = request.form.get('secret_key', '').strip()
        pass_phrase = request.form.get('pass_phrase', '').strip()
        bot_token = request.form.get('bot_token', '').strip()
        chat_id = request.form.get('chat_id', '').strip()

        logging.info("========== 用户填入信息 ==========")
        logging.info(f"交易对: {symbol} | 周期: {bar} | 金额: {order_size_str} | 模式: {trade_mode}")
        logging.info(f"API Key: {api_key[:6]}...{api_key[-4:] if len(api_key)>10 else ''}")
        logging.info(f"策略长度: {len(strategy_code)} 字符")

        if not all([api_key, secret_key, pass_phrase, bot_token, chat_id]):
            return render_template_string(
                HTML_TEMPLATE,
                error="请填写所有配置!",
                default_code=strategy_code or DEFAULT_PINE_EXAMPLE,
                api_key=api_key, secret_key=secret_key, pass_phrase=pass_phrase,
                bot_token=bot_token, chat_id=chat_id,
                symbol=symbol, bar=bar, order_size=order_size_str,
                configs=configs, configs_json=json.dumps(configs_json), last_config_index=last_config_index
            )

        if not strategy_code.strip():
            return render_template_string(
                HTML_TEMPLATE,
                error="策略代码不能为空!",
                default_code=strategy_code,
                api_key=api_key, secret_key=secret_key, pass_phrase=pass_phrase,
                bot_token=bot_token, chat_id=chat_id,
                symbol=symbol, bar=bar, order_size=order_size_str,
                configs=configs, configs_json=json.dumps(configs_json), last_config_index=last_config_index
            )

        try:
            order_size = float(order_size_str)
            if order_size <= 0: raise ValueError
        except:
            return render_template_string(
                HTML_TEMPLATE,
                error="下单金额必须是正数!",
                default_code=strategy_code,
                api_key=api_key, secret_key=secret_key, pass_phrase=pass_phrase,
                bot_token=bot_token, chat_id=chat_id,
                symbol=symbol, bar=bar, order_size=order_size_str,
                configs=configs, configs_json=json.dumps(configs_json), last_config_index=last_config_index
            )

        try:
            logging.info("开始转换策略代码...")
            CONVERTED_STRATEGY_CODE = convert_strategy_code(strategy_code)
            logging.info(f"转换成功，长度: {len(CONVERTED_STRATEGY_CODE)}")
        except ValueError as e:
            error_msg = f"策略转换错误: {str(e)}"
            logging.error(error_msg)
            return render_template_string(
                HTML_TEMPLATE,
                error=error_msg,
                default_code=strategy_code,
                api_key=api_key, secret_key=secret_key, pass_phrase=pass_phrase,
                bot_token=bot_token, chat_id=chat_id,
                symbol=symbol, bar=bar, order_size=order_size_str,
                configs=configs, configs_json=json.dumps(configs_json), last_config_index=last_config_index
            )
        except Exception as e:
            error_msg = f"转换异常: {str(e)}"
            logging.error(f"{error_msg}\n{traceback.format_exc()}")
            return render_template_string(
                HTML_TEMPLATE,
                error=error_msg,
                default_code=strategy_code,
                api_key=api_key, secret_key=secret_key, pass_phrase=pass_phrase,
                bot_token=bot_token, chat_id=chat_id,
                symbol=symbol, bar=bar, order_size=order_size_str,
                configs=configs, configs_json=json.dumps(configs_json), last_config_index=last_config_index
            )

        save_config_history(api_key, secret_key, pass_phrase, bot_token, chat_id)

        SYMBOL = symbol
        BAR_INTERVAL = bar
        ORDER_SIZE = order_size
        API_KEY = api_key
        SECRET_KEY = secret_key
        PASS_PHRASE = pass_phrase
        BOT_TOKEN = bot_token
        CHAT_ID = chat_id
        USER_STRATEGY_CODE = strategy_code

        BOT_RUNNING = True
        save_bot_state()
        BOT_THREAD = Thread(target=run_bot, daemon=True)
        BOT_THREAD.start()

        mode = "模拟盘" if IS_DEMO else "实盘"
        logging.info(f"策略启动成功 | {mode}")
        return render_template_string(
            HTML_TEMPLATE,
            success=True,
            symbol=SYMBOL, bar=BAR_INTERVAL, order_size=ORDER_SIZE, mode=mode,
            configs=configs, configs_json=json.dumps(configs_json), last_config_index=last_config_index
        )

    if is_running and BOT_RUNNING:
        mode = "模拟盘" if IS_DEMO else "实盘"
        return render_template_string(
            HTML_TEMPLATE,
            success=True,
            symbol=SYMBOL, bar=BAR_INTERVAL, order_size=ORDER_SIZE, mode=mode,
            configs=configs, configs_json=json.dumps(configs_json), last_config_index=last_config_index
        )

    return render_template_string(
        HTML_TEMPLATE,
        default_code=USER_STRATEGY_CODE or DEFAULT_PINE_EXAMPLE,
        configs=configs, configs_json=json.dumps(configs_json),
        symbol=SYMBOL, bar=BAR_INTERVAL, order_size=str(ORDER_SIZE), demo=IS_DEMO, last_config_index=last_config_index
    )

@app.route('/cancel', methods=['POST'])
def cancel():
    global BOT_RUNNING, BOT_THREAD, IS_DEMO, USER_STRATEGY_CODE

    BOT_RUNNING = False
    close_position()
    mode = "模拟盘" if IS_DEMO else "实盘"
    send_telegram_message(f"{mode}策略已取消")

    if BOT_THREAD:
        BOT_THREAD.join(timeout=5)
    BOT_THREAD = None

    last_strategy = USER_STRATEGY_CODE
    if os.path.exists(STATE_FILE):
        try:
            os.remove(STATE_FILE)
        except:
            pass

    # 强制刷新页面
    configs = [(i, c) for i, c in enumerate(load_config_history())]
    configs_json = [c["full_config"] for c in load_config_history()]
    last_config_index = configs[-1][0] if configs else -1
    USER_STRATEGY_CODE = last_strategy

    # 返回带刷新脚本的 HTML
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>策略已取消</title>
        <meta http-equiv="refresh" content="1;url=/">
        <style>
            body{font-family:Arial;background:#f4f4f4;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
            .msg{background:#fff;padding:30px;border-radius:10px;box-shadow:0 0 10px rgba(0,0,0,.1);text-align:center}
            .spinner{border:4px solid #f3f3f3;border-top:4px solid #28a745;border-radius:50%;width:30px;height:30px;animation:s 1s linear infinite;margin:0 auto 15px}
            @keyframes s{{0%{{transform:rotate(0)}}100%{{transform:rotate(360deg)}}}}
        </style>
    </head>
    <body>
        <div class="msg">
            <div class="spinner"></div>
            <h2>策略已取消</h2>
            <p>页面即将刷新...</p>
        </div>
    </body>
    </html>
    '''


@app.route('/health')
def health():
    return "OK", 200

if load_bot_state() and BOT_RUNNING and CONVERTED_STRATEGY_CODE:
    BOT_THREAD = Thread(target=run_bot, daemon=True)
    BOT_THREAD.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
