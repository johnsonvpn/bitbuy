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
RSI_OVERBOUGHT = 70  # RSI 超买阈值
RSI_OVERSOLD = 30    # RSI 超卖阈值
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

def get_latest_price_and_indicators(symbol: str) -> tuple:
    """获取最新价格、交易量、上下影线、振幅百分比、RSI、MA、EMA 和均线位置"""
    for attempt in range(3):
        try:
            flag = "1" if IS_DEMO else "0"
            market = MarketData.MarketAPI(flag=flag)
            ticker_data = market.get_ticker(instId=symbol)
            if ticker_data.get("code") != "0":
                logging.warning(f"Ticker API 失败: {ticker_data.get('msg')}")
                time.sleep(2)
                continue
            price = float(ticker_data["data"][0]["last"])
            
            url = f"https://www.okx.com/api/v5/market/history-candles?instId={symbol}&bar=1m&limit={CANDLE_LIMIT}"
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
                    f"RSI: {rsi_str}, MA20: {ma20_str}, 位置: {position}, 平均成交量: {avg_volume}"
                )
                
                logging.info(log_msg)
                return price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position, close, prev_close, avg_volume, open_price, high, low
            else:
                logging.warning(f"K线 API 失败: {candles_data.get('msg')}")
                time.sleep(2)
                continue
        except Exception as e:
            logging.warning(f"获取数据失败 (尝试 {attempt + 1}/3): {e}")
            time.sleep(2)
    return None

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
    logging.info("🚀 启动 OKX 自动交易机器人...")
    print("启动交易机器人...")
    send_telegram_message("🤖 交易机器人已启动！开始监控 BTC/USDT-SWAP 并执行交易。")

    current_position = None  # 当前持仓状态: None, "long", "short"
    entry_price = 0.0  # 入场价格
    last_signal = None  # 上一次交易信号
    stop_loss = 0.0  # 止损价格
    take_profit = 0.0  # 止盈价格
    last_candle_ts = 0  # 上一次K线时间戳
    recorded_rsi = None  # 记录的RSI值
    recorded_candle = None  # 记录的上一个K线数据，用于振幅和影线判断

    while True:
        try:
            data = get_latest_price_and_indicators(SYMBOL)
            if data is None:
                logging.error(f"无法获取 {SYMBOL} 的价格、交易量或指标，API 调用失败")
                print(f"错误: 无法获取 {SYMBOL} 的价格、交易量或指标，API 调用失败")
                send_telegram_message(f"❌ 程序错误: 无法获取 {SYMBOL} 的数据，API 调用失败")
                time.sleep(60)
                continue

            price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position, close, prev_close, avg_volume, open_price, high, low = data

            # 判断是否为新K线结束
            current_ts = int(time.time() // 60 * 60)  # 当前分钟开始时间戳
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
                    "open": open_price,  # 当前K线开盘价
                    "close": close,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "upper_shadow": upper_shadow,
                    "lower_shadow": lower_shadow,
                    "amplitude_percent": amplitude_percent
                }

                # 检查均线位置和RSI，记录RSI值
                if position == "在所有均线之上" and rsi is not None and rsi > RSI_OVERBOUGHT:
                    recorded_rsi = rsi
                    logging.info(f"记录RSI: {recorded_rsi:.2f} (超买，K线在所有均线之上)")
                elif position == "在所有均线之下" and rsi is not None and rsi < RSI_OVERSOLD:
                    recorded_rsi = rsi
                    logging.info(f"记录RSI: {recorded_rsi:.2f} (超卖，K线在所有均线之下)")
                else:
                    recorded_rsi = None  # 重置RSI记录

            # 在新K线开始时，检查是否满足交易条件（基于上一根K线）
            else:
                if recorded_rsi is not None and recorded_candle is not None and rsi is not None:
                    # 计算上一根K线的实体长度
                    candle_body = abs(recorded_candle["close"] - recorded_candle["open"])
                    candle_body = max(candle_body, 0.0001)  # 避免除以零
                    upper_shadow_ratio = recorded_candle["upper_shadow"] / candle_body
                    lower_shadow_ratio = recorded_candle["lower_shadow"] / candle_body

                    # 做空条件
                    if recorded_rsi > RSI_OVERBOUGHT and rsi < recorded_rsi and recorded_candle["amplitude_percent"] > MIN_AMPLITUDE_PERCENT and recorded_candle["volume"] > avg_volume and upper_shadow_ratio > MIN_SHADOW_RATIO:
                        signal = "sell"
                        msg = f"⚠️ 做空信号: 上一根K线振幅: {recorded_candle['amplitude_percent']:.2f}%, 成交量: {recorded_candle['volume']} (平均: {avg_volume}), 上影线比例: {upper_shadow_ratio:.2f}, RSI: {rsi:.2f} < 记录RSI: {recorded_rsi:.2f}"
                        logging.info(msg)
                        print(msg)
                        send_telegram_message(msg)

                    # 做多条件
                    elif recorded_rsi < RSI_OVERSOLD and rsi > recorded_rsi and recorded_candle["amplitude_percent"] > MIN_AMPLITUDE_PERCENT and recorded_candle["volume"] > avg_volume and lower_shadow_ratio > MIN_SHADOW_RATIO:
                        signal = "buy"
                        msg = f"⚠️ 做多信号: 上一根K线振幅: {recorded_candle['amplitude_percent']:.2f}%, 成交量: {recorded_candle['volume']} (平均: {avg_volume}), 下影线比例: {lower_shadow_ratio:.2f}, RSI: {rsi:.2f} > 记录RSI: {recorded_rsi:.2f}"
                        logging.info(msg)
                        print(msg)
                        send_telegram_message(msg)

            # 测试模式逻辑
            if TEST_MODE and AUTO_TRADE_ENABLED:
                import random
                test_signal = "buy" if random.random() > 0.5 else "sell"
                msg = f"🧪 测试模式下单: 信号={test_signal} | 价格={price} | 数量={ORDER_SIZE}"
                logging.info(msg)
                print(msg)
                send_telegram_message(msg)
                
                order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                
                if test_signal == "buy" and current_position != "long":
                    stop_loss = price * (1 - STOP_LOSS_PERCENT)
                    take_profit = price * (1 + TAKE_PROFIT_PERCENT)
                    order = place_order("buy", price, order_size, stop_loss, take_profit)
                    if order:
                        current_position = "long"
                        entry_price = price
                        last_signal = test_signal
                        recorded_rsi = None
                elif test_signal == "sell" and current_position != "short":
                    stop_loss = price * (1 + STOP_LOSS_PERCENT)
                    take_profit = price * (1 - TAKE_PROFIT_PERCENT)
                    order = place_order("sell", price, order_size, stop_loss, take_profit)
                    if order:
                        current_position = "short"
                        entry_price = price
                        last_signal = test_signal
                        recorded_rsi = None

            # 正常交易逻辑
            elif AUTO_TRADE_ENABLED and signal and signal != last_signal:
                order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)

                # 如果有持仓，先平仓
                if current_position == "long" and signal == "sell":
                    order = place_order("sell", price, order_size)
                    if order:
                        send_telegram_message(f"🛑 平仓: 卖出多单 | 价格={price}")
                        current_position = None
                elif current_position == "short" and signal == "buy":
                    order = place_order("buy", price, order_size)
                    if order:
                        send_telegram_message(f"🛑 平仓: 买入平空 | 价格={price}")
                        current_position = None

                # 执行新开仓
                if signal == "buy" and current_position is None:
                    stop_loss = price * (1 - STOP_LOSS_PERCENT)
                    take_profit = price * (1 + TAKE_PROFIT_PERCENT)
                    order = place_order("buy", price, order_size, stop_loss, take_profit)
                    if order:
                        current_position = "long"
                        entry_price = price
                        last_signal = signal
                        recorded_rsi = None  # 重置RSI记录
                elif signal == "sell" and current_position is None:
                    stop_loss = price * (1 + STOP_LOSS_PERCENT)
                    take_profit = price * (1 - TAKE_PROFIT_PERCENT)
                    order = place_order("sell", price, order_size, stop_loss, take_profit)
                    if order:
                        current_position = "short"
                        entry_price = price
                        last_signal = signal
                        recorded_rsi = None  # 重置RSI记录

            # 止损/止盈检查
            if current_position == "long":
                if price <= stop_loss:
                    order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                    order = place_order("sell", price, order_size)
                    if order:
                        send_telegram_message(f"🛑 止损卖出: 价格={price}")
                        current_position = None
                        last_signal = None
                        recorded_rsi = None
                elif price >= take_profit:
                    order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                    order = place_order("sell", price, order_size)
                    if order:
                        send_telegram_message(f"🎯 止盈卖出: 价格={price}")
                        current_position = None
                        last_signal = None
                        recorded_rsi = None
            elif current_position == "short":
                if price >= stop_loss:
                    order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                    order = place_order("buy", price, order_size)
                    if order:
                        send_telegram_message(f"🛑 止损买入: 价格={price}")
                        current_position = None
                        last_signal = None
                        recorded_rsi = None
                elif price <= take_profit:
                    order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)
                    order = place_order("buy", price, order_size)
                    if order:
                        send_telegram_message(f"🎯 止盈买入: 价格={price}")
                        current_position = None
                        last_signal = None
                        recorded_rsi = None

            # 动态调整检查频率
            if signal:
                time.sleep(COOLDOWN)
            else:
                time.sleep(CHECK_INTERVAL)

        except Exception as e:
            logging.error(f"程序错误: {e}")
            print(f"错误: {e}")
            send_telegram_message(f"❌ 程序错误: {e}")
            time.sleep(60)