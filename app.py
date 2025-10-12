import time
import requests
import logging
import pandas as pd
from okx import MarketData, Trade, Account
import uuid
from datetime import datetime, timezone, timedelta
from flask import Flask
from threading import Thread
import os
from urllib3.exceptions import NameResolutionError
from tenacity import retry, stop_after_attempt, wait_exponential

# ============ 配置区域 ============

# 从 Hugging Face Spaces Secrets 读取环境变量
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
PASS_PHRASE = os.getenv("PASS_PHRASE")

# 验证环境变量
if not all([BOT_TOKEN, CHAT_ID, API_KEY, SECRET_KEY, PASS_PHRASE]):
    logging.error("缺少必要的环境变量（BOT_TOKEN, CHAT_ID, API_KEY, SECRET_KEY, PASS_PHRASE）")
    raise EnvironmentError("缺少必要的环境变量")

IS_DEMO = True
AUTO_TRADE_ENABLED = True
TEST_MODE = False
TEST_CLOSE_POSITION = False
ONLY_TEST_CLOSE = False
SYMBOL = "BTC-USDT-SWAP"
CHECK_INTERVAL = 5
COOLDOWN = 1800  # 30分钟冷却期
ORDER_SIZE = 0.1
MIN_ORDER_SIZE = 0.001
RSI_PERIOD = 14
MA_PERIODS = [20, 60, 120]
CANDLE_LIMIT = max(MA_PERIODS) + 20
BAR_INTERVAL = "5m"  # 5分钟周期
RSI_OVERBOUGHT = 80
RSI_OVERSOLD = 20
STOP_LOSS_PERCENT = 0.02
TAKE_PROFIT_PERCENT = 0.04
MIN_AMPLITUDE_PERCENT = 2.0
MIN_SHADOW_RATIO = 1.0
MIN_PROFIT = 0.1  # 最小盈利阈值 USDT
MESSAGE_COUNT = 0  # 每日消息计数器
MESSAGE_LIMIT = 100  # 每日消息上限

# 确保日志目录存在
LOG_DIR = "/tmp"  # 使用 /tmp 目录，Hugging Face 通常允许写入
LOG_FILE = os.path.join(LOG_DIR, "combined_trading_bot.log")
# 检查目录并尝试创建
try:
    os.makedirs(LOG_DIR, exist_ok=True)  # 创建目录（如果不存在）
except Exception as e:
    print(f"无法创建日志目录 {LOG_DIR}: {str(e)}")

# 配置日志
try:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(funcName)s - %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, mode='a'),  # 写入 /tmp 日志文件
            logging.StreamHandler()  # 同时输出到控制台
        ]
    )
    logging.info(f"日志配置成功，写入文件: {LOG_FILE}")
except PermissionError as e:
    print(f"无法写入日志文件 {LOG_FILE}: {str(e)}")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(funcName)s - %(message)s",
        handlers=[logging.StreamHandler()]  # 回退到仅控制台
    )
    logging.warning("日志文件写入失败，仅使用控制台输出")

app = Flask(__name__)

# Flask 健康检查端点
@app.route('/health', methods=['GET'])
def health():
    logging.info("进入健康检查端点")
    return "OK", 200

@app.route('/', methods=['GET'])
def index():
    logging.info("进入首页端点")
    return "Trading Bot Running on Hugging Face Spaces", 200

# ============ 功能函数 ============

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def send_telegram_message(message: str):
    global MESSAGE_COUNT
    logging.info(f"进入 send_telegram_message, 消息: {message}")
    try:
        MESSAGE_COUNT += 1
        if MESSAGE_COUNT > MESSAGE_LIMIT:
            logging.warning(f"每日消息数 {MESSAGE_COUNT} 超过上限 {MESSAGE_LIMIT}，跳过发送")
            return False

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 429:
            retry_after = response.json().get('parameters', {}).get('retry_after', 60)
            logging.warning(f"限流: 等待 {retry_after} 秒重试")
            time.sleep(retry_after)
            raise Exception("Retry due to rate limit")
        if response.status_code != 200:
            logging.error(f"发送失败: 状态码 {response.status_code}, 响应: {response.text}")
            return False
        logging.info("Telegram 消息发送成功")
        return True
    except NameResolutionError as e:
        logging.error(f"DNS 解析失败: {str(e)}")
        try:
            response = requests.post("https://149.154.167.220/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=10)
            if response.status_code != 200:
                logging.error(f"备用 DNS 发送失败: {response.status_code}, {response.text}")
                return False
            logging.info("备用 DNS 发送成功")
            return True
        except Exception as e2:
            logging.error(f"备用 DNS 失败: {str(e2)}")
            return False
    except Exception as e:
        logging.error(f"发送异常: {str(e)}")
        return False

def calculate_rsi(data, periods=RSI_PERIOD):
    logging.info(f"进入 calculate_rsi, 数据长度: {len(data)}, 周期: {periods}")
    try:
        reversed_data = data[::-1]
        closes = pd.Series([float(candle[4]) for candle in reversed_data])
        delta = closes.diff()
        up = delta.clip(lower=0).rolling(window=periods).mean()
        down = -delta.clip(upper=0).rolling(window=periods).mean()
        rs = up / down
        rsi = pd.Series(100 - (100 / (1 + rs)), index=closes.index)
        latest_rsi = rsi.iloc[-1]
        if pd.isna(latest_rsi):
            logging.warning("RSI 计算结果为 NaN")
            return None
        if down.iloc[-1] == 0:
            latest_rsi = 100
        logging.info(f"RSI 计算成功: {latest_rsi:.2f}")
        return latest_rsi
    except Exception as e:
        logging.error(f"RSI 计算失败: {str(e)}")
        return None

def calculate_ma_ema(data, periods):
    logging.info(f"进入 calculate_ma_ema, 数据长度: {len(data)}, 周期: {periods}")
    try:
        reversed_data = data[::-1]
        closes = pd.Series([float(candle[4]) for candle in reversed_data])
        ma = {f"MA{p}": closes.rolling(window=p).mean().iloc[-1] for p in periods}
        ema = {f"EMA{p}": closes.ewm(span=p, adjust=False).mean().iloc[-1] for p in periods}
        logging.info("MA/EMA 计算成功")
        return ma, ema
    except Exception as e:
        logging.error(f"MA/EMA 计算失败: {str(e)}")
        return {}, {}

def calculate_ma_concentration(ma, ema):
    logging.info(f"进入 calculate_ma_concentration")
    all_lines = [line for line in list(ma.values()) + list(ema.values()) if not pd.isna(line)]
    logging.info(f"参与计算的均线值: {len(all_lines)} 条")
    if len(all_lines) < 2:
        logging.warning("有效均线数量不足，无法计算密集度")
        return float('inf')
    max_diff = max(all_lines) - min(all_lines)
    logging.info(f"均线密集度计算成功: {max_diff:.2f}")
    return max_diff

def calculate_avg_volume(data, periods=10):
    logging.info(f"进入 calculate_avg_volume, 数据长度: {len(data)}, 周期: {periods}")
    try:
        reversed_data = data[::-1]
        volumes = pd.Series([float(candle[5]) for candle in reversed_data])
        avg_volume = volumes.rolling(window=periods).mean().iloc[-1]
        logging.info(f"平均成交量计算成功")
        return avg_volume
    except Exception as e:
        logging.error(f"平均成交量计算失败: {str(e)}")
        return None

def determine_position(close, ma, ema):
    logging.info(f"进入 determine_position, 收盘价: {close}")
    all_lines = [line for line in list(ma.values()) + list(ema.values()) if not pd.isna(line)]
    if not all_lines:
        logging.warning("无有效均线数据")
        return "无有效均线"
    if all(close > line for line in all_lines):
        logging.info("收盘价在所有均线之上")
        return "在所有均线之上"
    elif all(close < line for line in all_lines):
        logging.info("收盘价在所有均线之下")
        return "在所有均线之下"
    else:
        logging.info("收盘价在均线之间")
        return "在均线之间"

def get_interval_seconds(interval: str) -> int:
    logging.info(f"进入 get_interval_seconds, 周期: {interval}")
    interval_map = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600, "12H": 43200,
        "1D": 86400
    }
    seconds = interval_map.get(interval, 60)
    logging.info(f"周期转换成功: {seconds}秒")
    return seconds

def get_account_config():
    logging.info("进入 get_account_config")
    try:
        flag = "1" if IS_DEMO else "0"
        account = Account.AccountAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        result = account.get_account_config()
        if result.get("code") == "0" and result.get("data"):
            logging.info("账户配置查询成功")
            return result["data"][0]
        else:
            error_details = result.get("msg", "未知错误")
            logging.error(f"查询账户配置失败: {error_details}")
            return {}
    except Exception as e:
        logging.error(f"查询账户配置异常: {str(e)}")
        return {}

def get_positions():
    logging.info("进入 get_positions")
    try:
        flag = "1" if IS_DEMO else "0"
        account = Account.AccountAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        result = account.get_positions(instId=SYMBOL)
        if result.get("code") == "0" and result.get("data"):
            logging.info("持仓查询成功")
            return result["data"]
        else:
            error_details = result.get("msg", "未知错误")
            logging.error(f"查询持仓失败: {error_details}")
            return []
    except Exception as e:
        logging.error(f"查询持仓异常: {str(e)}")
        return []

def get_latest_price_and_indicators(symbol: str, fetch_candles=True) -> tuple:
    logging.info(f"进入 get_latest_price_and_indicators, 产品: {symbol}, 获取K线: {fetch_candles}")
    attempt = 0
    max_attempts = 5
    while attempt < max_attempts:
        try:
            attempt += 1
            flag = "1" if IS_DEMO else "0"
            market = MarketData.MarketAPI(flag=flag)
            ticker_data = market.get_ticker(instId=symbol)
            if ticker_data.get("code") != "0":
                logging.warning(f"Ticker API 失败 (尝试 {attempt}): {ticker_data.get('msg')}")
                time.sleep(2)
                continue
            price = float(ticker_data["data"][0]["last"])
            logging.info("价格获取成功")
            
            if not fetch_candles:
                logging.info("仅获取价格，跳过K线数据")
                return (price, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None)
            
            marketDataAPI = MarketData.MarketAPI(flag=flag)
            result = marketDataAPI.get_history_candlesticks(
                instId=symbol,
                bar=BAR_INTERVAL,
                limit=str(CANDLE_LIMIT)
            )
            if result.get("code") == "0" and result.get("data"):
                logging.info("K线数据获取成功")
                candle = result["data"][0]
                candle_ts = int(candle[0]) // 1000
                prev_candle = result["data"][1] if len(result["data"]) > 1 else candle
                open_price = float(candle[1])
                high = float(candle[2])
                low = float(candle[3])
                close = float(candle[4])
                volume = float(candle[5])
                prev_close = float(prev_candle[4])
                
                upper_shadow = high - max(open_price, close)
                lower_shadow = min(open_price, close) - low
                amplitude_percent = (high - low) / low * 100 if low != 0 else 0.0
                rsi = calculate_rsi(result["data"])
                ma, ema = calculate_ma_ema(result["data"], MA_PERIODS)
                position = determine_position(close, ma, ema)
                avg_volume = calculate_avg_volume(result["data"])
                ma_concentration = calculate_ma_concentration(ma, ema)
                
                logging.info("指标计算完成")
                return price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position, close, prev_close, avg_volume, open_price, high, low, ma_concentration
            else:
                logging.warning(f"K线 API 失败 (尝试 {attempt}): {result.get('msg')}")
                time.sleep(2)
                continue
        except Exception as e:
            logging.warning(f"获取数据失败 (尝试 {attempt}): {str(e)}")
            time.sleep(2)
            continue
    logging.error(f"达到最大尝试次数 {max_attempts}，无法获取数据")
    return None

def place_order(side: str, price: float, size: float, stop_loss: float = None, take_profit: float = None):
    logging.info(f"进入 place_order, side: {side}, 价格: {price}, 数量: {size}, 止损: {stop_loss}, 止盈: {take_profit}")
    try:
        flag = "1" if IS_DEMO else "0"
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        pos_side = "long" if side == "buy" else "short"
        order_id = str(int(time.time() * 1000)) + str(uuid.uuid4())[:8]
        logging.info(f"尝试下单: {side.upper()}, 价格: {price}, 数量: {size}, 订单ID: {order_id}")
        
        sz = str(size)
        if float(sz) <= 0:
            error_msg = f"下单数量必须大于0，当前数量: {sz}"
            logging.error(error_msg)
            return None
            
        order = trade.place_order(
            instId=SYMBOL,
            tdMode="cross",
            side=side,
            posSide=pos_side,
            ordType="market",
            sz=sz,
        )
        if order.get("code") == "0" and order.get("data") and order["data"][0].get("sCode") == "0":
            msg = f"✅ 下单成功: {side.upper()} | 止损: {stop_loss:.2f} | 止盈: {take_profit:.2f}"
            logging.info(msg)
            send_telegram_message(msg)
            return order
        else:
            error_details = order.get("data")[0].get("sMsg", "") or order.get("msg", "") if order.get("data") else order.get("msg", "未知错误")
            error_msg = f"下单失败: {side.upper()}, 错误: {error_details}"
            logging.error(error_msg)
            send_telegram_message(f"❌ {error_msg}")
            return None
    except Exception as e:
        error_msg = f"下单异常: {side.upper()}, 异常: {str(e)}"
        logging.error(error_msg)
        send_telegram_message(f"❌ {error_msg}")
        return None

def close_position():
    logging.info("进入 close_position")
    try:
        flag = "1" if IS_DEMO else "0"
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        order_id = str(int(time.time() * 1000)) + str(uuid.uuid4())[:8]
        
        account_config = get_account_config()
        pos_mode = account_config.get('posMode', 'unknown')
        logging.info(f"账户保证金模式查询成功")
        
        positions = get_positions()
        if not positions:
            msg = f"ℹ️ 无持仓可平"
            logging.info(msg)
            send_telegram_message(msg)
            return {"code": "0", "data": [], "msg": "无持仓"}
        
        success = False
        results = []
        for pos_side in ["long", "short"]:
            logging.info(f"尝试平仓: posSide={pos_side}, 订单ID: {order_id}")
            params = {
                "instId": SYMBOL,
                "mgnMode": "cross",
                "posSide": pos_side,
                "autoCxl": False,
                "clOrdId": order_id
            }
            result = trade.close_positions(**params)
            if result.get("code") == "0":
                if result.get("data") and len(result["data"]) > 0:
                    msg = f"✅ 平仓成功: posSide={pos_side}"
                    logging.info(msg)
                    send_telegram_message(msg)
                    success = True
                else:
                    msg = f"ℹ️ 平仓调用成功，但无 {pos_side} 持仓"
                    logging.info(msg)
                results.append(result)
            else:
                error_details = result.get("msg", "未知错误")
                error_msg = f"平仓失败: posSide={pos_side}, 错误代码: {result.get('code')}, 错误: {error_details}"
                logging.error(error_msg)
                send_telegram_message(f"❌ {error_msg}")
                results.append(result)
        
        if success:
            logging.info("至少一个持仓平仓成功")
            return {"code": "0", "data": results, "msg": "至少一个持仓平仓成功"}
        else:
            error_msg = f"平仓失败"
            logging.error(error_msg)
            send_telegram_message(f"❌ {error_msg}")
            return None
    except Exception as e:
        error_msg = f"平仓异常: {str(e)}"
        logging.error(error_msg)
        send_telegram_message(f"❌ {error_msg}")
        return None

def run_bot():
    logging.info(f"进入 run_bot, 配置: K线周期={BAR_INTERVAL}, 测试模式={TEST_MODE}")
    interval_secs = get_interval_seconds(BAR_INTERVAL)
    send_telegram_message(f"🤖 交易机器人启动！K线周期: {BAR_INTERVAL}, 测试模式: {TEST_MODE}")
    
    current_position = None
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    last_signal = None
    last_candle_ts = 0
    last_ma_position = "未知"
    recorded_candle = None
    test_mode_signal = "buy"
    last_price = 0.0
    last_trade_time = 0
    buy_confirm_count = 0
    sell_confirm_count = 0

    while True:
        try:
            logging.info("进入主循环")
            current_time = datetime.now(timezone.utc)
            current_timestamp = int(current_time.timestamp())
            cycle_start = (current_timestamp // interval_secs) * interval_secs
            seconds_to_next_cycle = (cycle_start + interval_secs) - current_timestamp
            if seconds_to_next_cycle > 0:
                time.sleep(seconds_to_next_cycle)

            price_data = get_latest_price_and_indicators(SYMBOL, fetch_candles=False)
            if price_data is None or len(price_data) == 0:
                logging.error(f"无法获取 {SYMBOL} 的价格，API 调用失败")
                send_telegram_message(f"❌ 程序错误: 无法获取 {SYMBOL} 的价格")
                time.sleep(60)
                continue

            current_price = price_data[0]
            price_change_percent = abs((current_price - last_price) / last_price * 100) if last_price > 0 else 0
            last_price = current_price

            current_ts = (int(time.time()) // interval_secs) * interval_secs
            is_new_candle = (current_ts != last_candle_ts)
            significant_price_change = price_change_percent > 0.5
            fetch_full_data = is_new_candle or significant_price_change

            if fetch_full_data:
                data = get_latest_price_and_indicators(SYMBOL, fetch_candles=True)
                if data is None:
                    logging.error(f"无法获取 {SYMBOL} 的完整数据，API 调用失败")
                    send_telegram_message(f"❌ 程序错误: 无法获取 {SYMBOL} 的完整数据")
                    time.sleep(60)
                    continue
            else:
                if current_position is not None:
                    if (current_position == "long" and current_price <= stop_loss) or \
                       (current_position == "short" and current_price >= stop_loss):
                        logging.info("触发止损平仓")
                        positions = get_positions()
                        if any(p["pos"] != "0" for p in positions):
                            result = close_position()
                            if result:
                                current_position = None
                                last_signal = None
                                last_trade_time = current_timestamp
                    elif (current_position == "long" and current_price >= take_profit) or \
                         (current_position == "short" and current_price <= take_profit):
                        logging.info("触发止盈平仓")
                        positions = get_positions()
                        if any(p["pos"] != "0" for p in positions):
                            result = close_position()
                            if result:
                                current_position = None
                                last_signal = None
                                last_trade_time = current_timestamp
                time.sleep(CHECK_INTERVAL)
                continue

            price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position, close, prev_close, avg_volume, open_price, high, low, ma_concentration = data

            beijing_tz = timezone(timedelta(hours=8))
            last_candle_utc = datetime.fromtimestamp(last_candle_ts, tz=timezone.utc) if last_candle_ts > 0 else None
            last_candle_time_str = last_candle_utc.astimezone(beijing_tz).strftime('%Y-%m-%d %H:%M:%S') if last_candle_utc else "N/A"
            current_time_str = datetime.fromtimestamp(current_ts, tz=timezone.utc).astimezone(beijing_tz).strftime('%Y-%m-%d %H:%M:%S')

            signal = None
            if ONLY_TEST_CLOSE:
                logging.info("进入只测试平仓模式")
                result = close_position()
                if result:
                    current_position = None
                    last_signal = None
                    last_trade_time = current_timestamp
            elif TEST_CLOSE_POSITION:
                logging.info("进入平仓测试")
                result = close_position()
                if result:
                    current_position = None
                    last_signal = None
                    last_trade_time = current_timestamp
            elif TEST_MODE:
                logging.info(f"进入测试模式, 当前信号: {test_mode_signal}")
                signal = test_mode_signal
                msg = f"⚠️ 测试模式信号: {signal.upper()}"
                send_telegram_message(msg)
                test_mode_signal = "sell" if test_mode_signal == "buy" else "buy"
            else:
                recorded_candle = {
                    "open": open_price,
                    "close": close,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "position": position
                }
                recorded_position = recorded_candle["position"]
                params_msg = (
                    f"下单参数检查: 当前位置: {position}, 上一位置: {recorded_position}, "
                    f"上次位置: {last_ma_position}, 上一K线 - 开盘: {recorded_candle['open']:.2f}, 收盘: {recorded_candle['close']:.2f}"
                )
                logging.info(params_msg)

                if recorded_position != last_ma_position and last_ma_position != "未知":
                    ma_concentration = calculate_ma_concentration(ma, ema)
                    concentration_threshold = close * 0.01
                    if recorded_position == "在所有均线之上":
                        buy_confirm_count += 1
                        sell_confirm_count = 0
                        if buy_confirm_count >= 2 and ma_concentration <= concentration_threshold and rsi < 50 and volume > avg_volume * 1.5:
                            signal = "buy"
                            msg = f"⚠️ 做多信号: 连续2根K线在所有均线之上，均线密集度: {ma_concentration:.2f}, RSI: {rsi:.2f}"
                            logging.info(msg)
                            send_telegram_message(msg)
                            buy_confirm_count = 0
                    elif recorded_position == "在所有均线之下":
                        sell_confirm_count += 1
                        buy_confirm_count = 0
                        if sell_confirm_count >= 2 and rsi > 50 and volume > avg_volume * 1.5:
                            signal = "sell"
                            msg = f"⚠️ 做空信号: 连续2根K线在所有均线之下，RSI: {rsi:.2f}"
                            logging.info(msg)
                            send_telegram_message(msg)
                            sell_confirm_count = 0
                    else:
                        buy_confirm_count = 0
                        sell_confirm_count = 0
                else:
                    buy_confirm_count = 0
                    sell_confirm_count = 0

                if recorded_position == "在均线之间":
                    logging.info("触发止盈平仓")
                    positions = get_positions()
                    if any(p["pos"] != "0" for p in positions):
                        result = close_position()
                        if result:
                            current_position = None
                            last_signal = None
                            last_trade_time = current_timestamp

                last_ma_position = recorded_position
                last_candle_ts = current_ts

            if AUTO_TRADE_ENABLED and signal and signal != last_signal and (current_timestamp - last_trade_time) >= COOLDOWN:
                order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                positions = get_positions()
                if any(p["pos"] != "0" for p in positions):
                    result = close_position()
                    if result:
                        current_position = None
                        last_trade_time = current_timestamp

                if signal == "buy" and current_position is None:
                    potential_profit = price * TAKE_PROFIT_PERCENT * order_size * 5
                    if potential_profit < MIN_PROFIT:
                        msg = f"⚠️ 跳过买入信号: 潜在盈利 {potential_profit:.2f} USDT < 最小盈利 {MIN_PROFIT} USDT"
                        logging.info(msg)
                        send_telegram_message(msg)
                    else:
                        stop_loss = price * (1 - STOP_LOSS_PERCENT)
                        take_profit = price * (1 + TAKE_PROFIT_PERCENT)
                        order = place_order("buy", price, order_size, stop_loss, take_profit)
                        if order:
                            current_position = "long"
                            entry_price = price
                            last_signal = signal
                            last_trade_time = current_timestamp
                elif signal == "sell" and current_position is None:
                    potential_profit = price * TAKE_PROFIT_PERCENT * order_size * 5
                    if potential_profit < MIN_PROFIT:
                        msg = f"⚠️ 跳过卖出信号: 潜在盈利 {potential_profit:.2f} USDT < 最小盈利 {MIN_PROFIT} USDT"
                        logging.info(msg)
                        send_telegram_message(msg)
                    else:
                        stop_loss = price * (1 + STOP_LOSS_PERCENT)
                        take_profit = price * (1 - TAKE_PROFIT_PERCENT)
                        order = place_order("sell", price, order_size, stop_loss, take_profit)
                        if order:
                            current_position = "short"
                            entry_price = price
                            last_signal = signal
                            last_trade_time = current_timestamp

        except Exception as e:
            logging.error(f"主循环异常: {str(e)}")
            send_telegram_message(f"❌ 主循环错误: {str(e)}")
            time.sleep(60)

if __name__ == "__main__":
    logging.info("启动 Flask 服务...")
    bot_thread = Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    app.run(host='0.0.0.0', port=7860)
import time
import requests
import logging
import pandas as pd
from okx import MarketData, Trade
import uuid
from datetime import datetime, timezone, timedelta

# ============ 配置区域 ============

# Telegram Bot 配置
BOT_TOKEN = "8239027160:AAGllh-w2_4mCI3B1oEPfQHgBeOiD6Zt3ZU"
CHAT_ID = "8024914547"  # Telegram Chat ID

# OKX API 配置
API_KEY = "c5788dfe-8ef0-4a07-812b-15c4c8f890b0"
SECRET_KEY = "B72E8E3BE0141966165B18DF9D3805E9"
PASS_PHRASE = "gamewell810DO*"

IS_DEMO = True  # True=模拟盘，False=实盘
AUTO_TRADE_ENABLED = True  # True=自动下单，False=仅发送提醒
TEST_MODE = False      # True=测试模式，不需要满足其他条件就可以下单
SYMBOL = "BTC-USDT-SWAP"  # 永续合约
CHECK_INTERVAL = 5  # 正常检查间隔（秒）
COOLDOWN = 50  # 触发后的冷却时间（秒）
ORDER_SIZE = 0.1  # 下单数量
MIN_ORDER_SIZE = 0.001  # 最小下单数量
RSI_PERIOD = 14  # RSI 计算周期
MA_PERIODS = [20, 60, 120]  # MA 和 EMA 周期
CANDLE_LIMIT = max(MA_PERIODS) + 10  # 多获取一些用于平均成交量
BAR_INTERVAL = "5m"  # K线周期，可调整: "1m", "5m", "15m", "1H", "1D" 等
RSI_OVERBOUGHT = 80  # RSI 超买阈值
RSI_OVERSOLD = 20    # RSI 超卖阈值
STOP_LOSS_PERCENT = 0.02  # 止损百分比 (2%)
TAKE_PROFIT_PERCENT = 0.04  # 止盈百分比 (4%)
MIN_AMPLITUDE_PERCENT = 2.0  # 最小振幅百分比
MIN_SHADOW_RATIO = 1.0  # 影线长度与实体长度的最小比例

# 配置日志
logging.basicConfig(
    filename="combined_trading_bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ============ 功能函数 ============

def send_telegram_message(message: str):
    """发送 Telegram 消息"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code != 200:
            logging.error(f"Telegram 消息发送失败: {response.text}")
    except Exception as e:
        logging.error(f"Telegram 消息发送错误: {e}")

def calculate_rsi(data, periods=RSI_PERIOD):
    """计算 RSI"""
    try:
        reversed_data = data[::-1]
        closes = pd.Series([float(candle[4]) for candle in reversed_data])
        delta = closes.diff()
        up = delta.clip(lower=0).rolling(window=periods).mean()
        down = -delta.clip(upper=0).rolling(window=periods).mean()
        rs = up / down
        rsi = pd.Series(100 - (100 / (1 + rs)), index=closes.index)
        latest_rsi = rsi.iloc[-1]
        if pd.isna(latest_rsi):
            return None
        if down.iloc[-1] == 0:
            latest_rsi = 100
        return latest_rsi
    except Exception as e:
        logging.error(f"RSI 计算失败: {e}")
        return None

def calculate_ma_ema(data, periods):
    """计算 MA 和 EMA"""
    try:
        reversed_data = data[::-1]
        closes = pd.Series([float(candle[4]) for candle in reversed_data])
        ma = {f"MA{p}": closes.rolling(window=p).mean().iloc[-1] for p in periods}
        ema = {f"EMA{p}": closes.ewm(span=p, adjust=False).mean().iloc[-1] for p in periods}
        return ma, ema
    except Exception as e:
        logging.error(f"MA/EMA 计算失败: {e}")
        return {}, {}

def calculate_avg_volume(data, periods=10):
    """计算近期平均成交量"""
    try:
        reversed_data = data[::-1]
        volumes = pd.Series([float(candle[5]) for candle in reversed_data])
        return volumes.rolling(window=periods).mean().iloc[-1]
    except Exception as e:
        logging.error(f"平均成交量计算失败: {e}")
        return None

def determine_position(close, ma, ema):
    """判断当前 K 线收盘价相对于均线的位置"""
    all_lines = [line for line in list(ma.values()) + list(ema.values()) if not pd.isna(line)]
    if not all_lines:
        return "无有效均线"
    if all(close > line for line in all_lines):
        return "在所有均线之上"
    elif all(close < line for line in all_lines):
        return "在所有均线之下"
    else:
        return "在均线之间"

def get_interval_seconds(interval: str) -> int:
    """根据K线周期字符串返回秒数"""
    interval_map = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600, "12H": 43200,
        "1D": 86400
    }
    return interval_map.get(interval, 60)  # 默认1m

def get_latest_price_and_indicators(symbol: str) -> tuple:
    """获取最新价格、交易量、上下影线、振幅百分比、RSI、MA、EMA 和均线位置，失败时持续重试"""
    attempt = 0
    while True:
        try:
            attempt += 1
            flag = "1" if IS_DEMO else "0"
            market = MarketData.MarketAPI(flag=flag)
            ticker_data = market.get_ticker(instId=symbol)
            if ticker_data.get("code") != "0":
                logging.warning(f"Ticker API 失败 (尝试 {attempt}): {ticker_data.get('msg')}")
                time.sleep(2)
                continue
            price = float(ticker_data["data"][0]["last"])
            
            url = f"https://www.okx.com/api/v5/market/history-candles?instId={symbol}&bar={BAR_INTERVAL}&limit={CANDLE_LIMIT}"
            response = requests.get(url, timeout=5)
            candles_data = response.json()
            if candles_data.get("code") == "0" and candles_data.get("data"):
                candle = candles_data["data"][0]
                prev_candle = candles_data["data"][1] if len(candles_data["data"]) > 1 else candle
                open_price = float(candle[1])
                high = float(candle[2])
                low = float(candle[3])
                close = float(candle[4])
                volume = float(candle[5])
                prev_close = float(prev_candle[4])
                
                upper_shadow = high - max(open_price, close)
                lower_shadow = min(open_price, close) - low
                amplitude_percent = (high - low) / low * 100 if low != 0 else 0.0
                rsi = calculate_rsi(candles_data["data"])
                ma, ema = calculate_ma_ema(candles_data["data"], MA_PERIODS)
                position = determine_position(close, ma, ema)
                avg_volume = calculate_avg_volume(candles_data["data"])
                
                ma20_str = f"{ma['MA20']:.2f}" if not pd.isna(ma['MA20']) else "N/A"
                rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
                
                log_msg = (
                    f"成功获取价格: {price}, 交易量: {volume}, 上影线: {upper_shadow}, "
                    f"下影线: {lower_shadow}, 振幅: {amplitude_percent:.2f}%, "
                    f"RSI: {rsi_str}, MA20: {ma20_str}, 位置: {position}, 平均成交量: {avg_volume}, K线周期: {BAR_INTERVAL}"
                )
                
                logging.info(log_msg)
                return price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position, close, prev_close, avg_volume, open_price, high, low
            else:
                logging.warning(f"K线 API 失败 (尝试 {attempt}): {candles_data.get('msg')}")
                time.sleep(2)
                continue
        except Exception as e:
            logging.warning(f"获取数据失败 (尝试 {attempt}): {e}")
            time.sleep(2)
            continue

def place_order(side: str, price: float, size: float, stop_loss: float = None, take_profit: float = None):
    """下单，仅在成功后推送Telegram消息"""
    try:
        flag = "1" if IS_DEMO else "0"
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        pos_side = "long" if side == "buy" else "short"
        order_id = str(int(time.time() * 1000)) + str(uuid.uuid4())[:8]
        logging.info(f"尝试下单: {side.upper()}, 价格: {price}, 数量: {size}, 订单ID: {order_id}")
        
        sz = str(size)
        if float(sz) <= 0:
            error_msg = f"下单数量必须大于0，当前数量: {sz}"
            logging.error(error_msg)
            return None
            
        order = trade.place_order(
            instId=SYMBOL,
            tdMode="cross",
            side=side,
            posSide=pos_side,
            ordType="market",
            sz=sz,
        )
        logging.info(f"API返回原始订单数据: {order}")
        if order.get("code") == "0" and order.get("data") and order["data"][0].get("sCode") == "0":
            msg = f"✅ 下单成功: {side.upper()} | 价格: {price} | 数量: {size} | 订单ID: {order_id}"
            if stop_loss and take_profit:
                msg += f" | 止损: {stop_loss:.2f} | 止盈: {take_profit:.2f}"
            logging.info(msg)
            send_telegram_message(msg)
            return order
        else:
            error_details = ""
            if order.get("data") and len(order["data"]) > 0:
                error_details = order["data"][0].get("sMsg", "") or order.get("msg", "")
            else:
                error_details = order.get("msg", "未知错误")
            error_msg = f"下单失败: {side.upper()}, 错误: {error_details}"
            logging.error(error_msg)
            send_telegram_message(f"❌ {error_msg}")
            return None
    except Exception as e:
        error_msg = f"下单异常: {side.upper()}, 异常: {str(e)}"
        logging.error(error_msg)
        send_telegram_message(f"❌ {error_msg}")
        return None

# ============ 主程序 ============

if __name__ == "__main__":
    interval_secs = get_interval_seconds(BAR_INTERVAL)
    logging.info(f"🚀 启动 OKX 自动交易机器人... K线周期: {BAR_INTERVAL} ({interval_secs}秒)")
    print(f"启动交易机器人... K线周期: {BAR_INTERVAL} ({interval_secs}秒)")
    send_telegram_message(f"🤖 交易机器人已启动！K线周期: {BAR_INTERVAL}，开始监控 BTC/USDT-SWAP 并执行交易。")

    current_position = None  # 当前持仓状态: None, "long", "short"
    entry_price = 0.0  # 入场价格
    stop_loss = 0.0  # 止损价格
    take_profit = 0.0  # 止盈价格
    last_signal = None  # 上一次交易信号
    last_candle_ts = 0  # 上一次K线时间戳
    recorded_rsi = None  # 记录的RSI值（用于开仓）
    recorded_rsi_profit = None  # 记录的RSI值（用于止盈）
    recorded_candle = None  # 记录的上一个K线数据
    is_profit_check = False  # 是否处于止盈检查状态

    while True:
        try:
            # 同步到下一个K线结束时间
            current_time = datetime.now(timezone.utc)
            current_timestamp = int(current_time.timestamp())
            # 计算当前周期内的偏移
            cycle_start = (current_timestamp // interval_secs) * interval_secs
            seconds_to_next_cycle = (cycle_start + interval_secs) - current_timestamp
            if seconds_to_next_cycle > 0:
                print(f"等待 {seconds_to_next_cycle} 秒到下一个 {BAR_INTERVAL} K线结束...")
                time.sleep(seconds_to_next_cycle)  # 等待到K线周期结束

            # 获取最新数据
            data = get_latest_price_and_indicators(SYMBOL)
            if data is None:
                logging.error(f"无法获取 {SYMBOL} 的价格、交易量或指标，API 调用失败")
                print(f"错误: 无法获取 {SYMBOL} 的价格、交易量或指标，API 调用失败")
                send_telegram_message(f"❌ 程序错误: 无法获取 {SYMBOL} 的数据，API 调用失败")
                time.sleep(60)
                continue

            price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position, close, prev_close, avg_volume, open_price, high, low = data

            # 判断是否为新K线结束（基于周期时间戳）
            current_ts = (int(time.time()) // interval_secs) * interval_secs  # 当前周期开始时间戳
            beijing_tz = timezone(timedelta(hours=8))
            last_candle_utc = datetime.fromtimestamp(last_candle_ts, tz=timezone.utc) if last_candle_ts > 0 else None
            last_candle_time_str = last_candle_utc.astimezone(beijing_tz).strftime('%Y-%m-%d %H:%M:%S') if last_candle_utc else "N/A"
            current_utc = datetime.fromtimestamp(current_ts, tz=timezone.utc)
            current_time_str = current_utc.astimezone(beijing_tz).strftime('%Y-%m-%d %H:%M:%S')

            signal = None
            if current_ts > last_candle_ts:
                last_candle_ts = current_ts
                # 记录当前K线数据，用于下一根K线的判断
                recorded_candle = {
                    "open": open_price,
                    "close": close,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "rsi": rsi
                }

                # 检查RSI用于开仓
                if rsi is not None:
                    if rsi > RSI_OVERBOUGHT:  # RSI > 80
                        recorded_rsi = rsi
                        is_profit_check = False
                        print(f"记录RSI: {recorded_rsi:.2f} (超买)")
                        logging.info(f"记录RSI: {recorded_rsi:.2f} (超买)")
                    elif rsi < RSI_OVERSOLD:  # RSI < 20
                        recorded_rsi = rsi
                        is_profit_check = False
                        print(f"记录RSI: {recorded_rsi:.2f} (超卖)")
                        logging.info(f"记录RSI: {recorded_rsi:.2f} (超卖)")
                    else:
                        recorded_rsi = None  # 重置RSI记录

                # 检查RSI用于止盈
                if current_position == "long" and rsi > 70:
                    recorded_rsi_profit = rsi
                    is_profit_check = True
                    print(f"记录止盈RSI: {recorded_rsi_profit:.2f} (多单)")
                    logging.info(f"记录止盈RSI: {recorded_rsi_profit:.2f} (多单)")
                elif current_position == "short" and rsi < 30:
                    recorded_rsi_profit = rsi
                    is_profit_check = True
                    print(f"记录止盈RSI: {recorded_rsi_profit:.2f} (空单)")
                    logging.info(f"记录止盈RSI: {recorded_rsi_profit:.2f} (空单)")

            # 检查开仓条件（基于上一根K线）
            else:
                if recorded_rsi is not None and recorded_candle is not None and rsi is not None:
                    mid_price = (recorded_candle["high"] + recorded_candle["low"]) / 2
                    # 打印下单参数
                    is_rise = recorded_candle["close"] > recorded_candle["open"]
                    is_fall = recorded_candle["close"] < recorded_candle["open"]
                    is_retrace_sell = recorded_candle["close"] < mid_price
                    is_retrace_buy = recorded_candle["close"] > mid_price
                    params_msg = (
                        f"下单参数检查: 当前RSI: {rsi:.2f}, 记录RSI: {recorded_rsi:.2f}, "
                        f"上一K线 - 开盘: {recorded_candle['open']:.2f}, 收盘: {recorded_candle['close']:.2f}, "
                        f"最高: {recorded_candle['high']:.2f}, 最低: {recorded_candle['low']:.2f}, "
                        f"中间价: {mid_price:.2f}, 是否上涨: {is_rise}, 是否下跌: {is_fall}, "
                        f"卖单回撤>50%: {is_retrace_sell}, 买单回撤>50%: {is_retrace_buy}"
                    )
                    print(params_msg)
                    logging.info(params_msg)

                    # 做空条件
                    sell_conditions = {
                        "RSI条件": recorded_rsi > RSI_OVERBOUGHT and rsi < recorded_rsi,
                        "K线上涨": is_rise,
                        "回撤>50%": is_retrace_sell
                    }
                    if all(sell_conditions.values()):
                        signal = "sell"
                        msg = f"⚠️ 做空信号: RSI: {rsi:.2f} < 记录RSI: {recorded_rsi:.2f}, 上一根K线上涨后回撤 > 50%"
                        logging.info(msg)
                        print(msg)
                        send_telegram_message(msg)
                    else:
                        failed_conditions = [k for k, v in sell_conditions.items() if not v]
                        msg = f"做空条件未满足: {', '.join(failed_conditions)}"
                        print(msg)
                        logging.info(msg)

                    # 做多条件
                    buy_conditions = {
                        "RSI条件": recorded_rsi < RSI_OVERSOLD and rsi > recorded_rsi,
                        "K线下跌": is_fall,
                        "回撤>50%": is_retrace_buy
                    }
                    if all(buy_conditions.values()):
                        signal = "buy"
                        msg = f"⚠️ 做多信号: RSI: {rsi:.2f} > 记录RSI: {recorded_rsi:.2f}, 上一根K线下跌后回撤 > 50%"
                        logging.info(msg)
                        print(msg)
                        send_telegram_message(msg)
                    else:
                        failed_conditions = [k for k, v in buy_conditions.items() if not v]
                        msg = f"做多条件未满足: {', '.join(failed_conditions)}"
                        print(msg)
                        logging.info(msg)

            # 检查止盈条件
            if is_profit_check and recorded_rsi_profit is not None and rsi is not None:
                if current_position == "long" and rsi < recorded_rsi_profit:
                    order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                    order = place_order("sell", price, order_size)
                    if order:
                        send_telegram_message(f"🎯 止盈卖出: 价格={price}, RSI: {rsi:.2f} < 记录RSI: {recorded_rsi_profit:.2f}")
                        current_position = None
                        last_signal = None
                        recorded_rsi = None
                        recorded_rsi_profit = None
                        is_profit_check = False
                elif current_position == "short" and rsi > recorded_rsi_profit:
                    order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                    order = place_order("buy", price, order_size)
                    if order:
                        send_telegram_message(f"🎯 止盈买入: 价格={price}, RSI: {rsi:.2f} > 记录RSI: {recorded_rsi_profit:.2f}")
                        current_position = None
                        last_signal = None
                        recorded_rsi = None
                        recorded_rsi_profit = None
                        is_profit_check = False

            # 输出当前状态
            rsi_display = f"{rsi:.2f}" if rsi is not None else "N/A"
            print(f"当前时间: {current_time_str} | 上一K线时间: {last_candle_time_str} | 收盘价格: {recorded_candle['close'] if recorded_candle else 'N/A'} | 位置: {position} | RSI: {rsi_display} | 记录RSI: {recorded_rsi if recorded_rsi else 'N/A'} | 止盈RSI: {recorded_rsi_profit if recorded_rsi_profit else 'N/A'} | 信号: {signal} | 持仓: {current_position}")

            # 交易逻辑
            if AUTO_TRADE_ENABLED and signal and signal != last_signal:
                order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                double_order_size = order_size * 2  # 加倍下单数量

                # 如果有持仓，先处理平仓或加倍
                if signal == "sell" and current_position == "long":
                    order = place_order("sell", price, order_size)
                    if order:
                        send_telegram_message(f"🛑 平仓: 卖出多单 | 价格={price}")
                        current_position = None
                elif signal == "buy" and current_position == "short":
                    order = place_order("buy", price, order_size)
                    if order:
                        send_telegram_message(f"🛑 平仓: 买入平空 | 价格={price}")
                        current_position = None

                # 执行新开仓或加倍
                if signal == "buy":
                    final_order_size = double_order_size if current_position == "long" else order_size
                    stop_loss = price * (1 - STOP_LOSS_PERCENT)
                    take_profit = price * (1 + TAKE_PROFIT_PERCENT)
                    order = place_order("buy", price, final_order_size, stop_loss, take_profit)
                    if order:
                        current_position = "long"
                        entry_price = price
                        last_signal = signal
                        recorded_rsi = None
                elif signal == "sell":
                    final_order_size = double_order_size if current_position == "short" else order_size
                    stop_loss = price * (1 + STOP_LOSS_PERCENT)
                    take_profit = price * (1 - TAKE_PROFIT_PERCENT)
                    order = place_order("sell", price, final_order_size, stop_loss, take_profit)
                    if order:
                        current_position = "short"
                        entry_price = price
                        last_signal = signal
                        recorded_rsi = None

            # 止损检查
            if current_position == "long" and price <= stop_loss:
                order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                order = place_order("sell", price, order_size)
                if order:
                    send_telegram_message(f"🛑 止损卖出: 价格={price}")
                    current_position = None
                    last_signal = None
                    recorded_rsi = None
                    recorded_rsi_profit = None
                    is_profit_check = False
            elif current_position == "short" and price >= stop_loss:
                order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                order = place_order("buy", price, order_size)
                if order:
                    send_telegram_message(f"🛑 止损买入: 价格={price}")
                    current_position = None
                    last_signal = None
                    recorded_rsi = None
                    recorded_rsi_profit = None
                    is_profit_check = False

        except Exception as e:
            logging.error(f"程序错误: {e}")
            print(f"错误: {e}")
            send_telegram_message(f"❌ 程序错误: {e}")
            time.sleep(60)