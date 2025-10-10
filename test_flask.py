import time
import requests
import logging
import pandas as pd
from okx import MarketData, Trade
import uuid
from datetime import datetime

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
SYMBOL = "BTC-USDT-SWAP"  # 永续合约
CHECK_INTERVAL = 5  # 正常检查间隔（秒）
COOLDOWN = 50  # 触发后的冷却时间（秒）
ORDER_SIZE = 0.1  # 下单数量
RSI_PERIOD = 14  # RSI 计算周期
MA_PERIODS = [20, 60, 120]  # MA 和 EMA 周期
CANDLE_LIMIT = max(MA_PERIODS) + 10  # 多获取一些用于平均成交量
RSI_OVERBOUGHT = 70  # RSI 超买阈值
RSI_OVERSOLD = 30    # RSI 超卖阈值
STOP_LOSS_PERCENT = 0.02  # 止损百分比 (2%)
TAKE_PROFIT_PERCENT = 0.04  # 止盈百分比 (4%)
SHADOW_RATIO = 2.0  # 影线相对于实体的比率阈值，用于判断顶部/底部形态
AMPLITUDE_THRESHOLD = 0.5  # 振幅百分比阈值（%），用于过滤小振幅K线

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
                prev_open = float(prev_candle[1])
                prev_high = float(prev_candle[2])
                prev_low = float(prev_candle[3])
                prev_close = float(prev_candle[4])
                
                upper_shadow = high - max(open_price, close)
                lower_shadow = min(open_price, close) - low
                prev_upper_shadow = prev_high - max(prev_open, prev_close)
                prev_lower_shadow = min(prev_open, prev_close) - prev_low
                amplitude_percent = (high - low) / low * 100 if low != 0 else 0.0
                prev_amplitude_percent = (prev_high - prev_low) / prev_low * 100 if prev_low != 0 else 0.0
                rsi = calculate_rsi(candles_data["data"])
                ma, ema = calculate_ma_ema(candles_data["data"], MA_PERIODS)
                position = determine_position(close, ma, ema)
                prev_position = determine_position(prev_close, ma, ema)  # 使用最新的ma/ema近似
                avg_volume = calculate_avg_volume(candles_data["data"])
                
                ma20_str = f"{ma['MA20']:.2f}" if not pd.isna(ma['MA20']) else "N/A"
                rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
                
                log_msg = (
                    f"成功获取价格: {price}, 交易量: {volume}, 上影线: {upper_shadow}, "
                    f"下影线: {lower_shadow}, 振幅: {amplitude_percent:.2f}%, "
                    f"RSI: {rsi_str}, MA20: {ma20_str}, 位置: {position}, 平均成交量: {avg_volume}"
                )
                
                logging.info(log_msg)
                return (price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema,
                        position, close, prev_close, avg_volume, prev_upper_shadow, prev_lower_shadow,
                        prev_amplitude_percent, prev_position, prev_open)
            else:
                logging.warning(f"K线 API 失败: {candles_data.get('msg')}")
                time.sleep(2)
                continue
        except Exception as e:
            logging.warning(f"获取数据失败 (尝试 {attempt + 1}/3): {e}")
            time.sleep(2)
    return None  # 避免抛出异常，改为返回 None

def place_order(side: str, price: float, size: float, stop_loss: float = None, take_profit: float = None):
    """下单，仅在成功后推送Telegram消息"""
    try:
        flag = "1" if IS_DEMO else "0"
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        pos_side = "long" if side == "buy" else "short"
        order_id = str(uuid.uuid4())
        logging.info(f"尝试下单: {side.upper()}, 价格: {price}, 数量: {size}, 订单ID: {order_id}")
        order = trade.place_order(
            instId=SYMBOL,
            tdMode="cross",
            side=side,
            posSide=pos_side,
            ordType="market",
            sz=str(size),
            clOrdId=order_id
        )
        if order.get("code") == "0" and order.get("data") and order["data"][0].get("sCode") == "0":
            msg = f"✅ 下单成功: {side.upper()} | 价格: {price} | 数量: {size} | 订单ID: {order_id}"
            if stop_loss and take_profit:
                msg += f" | 止损: {stop_loss:.2f} | 止盈: {take_profit:.2f}"
            logging.info(msg)
            send_telegram_message(msg)
            return order
        else:
            error_msg = f"下单失败: {side.upper()}, 错误: {order.get('msg') or order['data'][0].get('sMsg')}"
            logging.error(error_msg)
            return None
    except Exception as e:
        error_msg = f"下单失败: {side.upper()}, 错误: {e}"
        logging.error(error_msg)
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
    last_candle_ts = 0  # 上一次K线时间戳，用于判断K线结束

    while True:
        try:
            data = get_latest_price_and_indicators(SYMBOL)
            if data is None:
                logging.error(f"无法获取 {SYMBOL} 的价格、交易量或指标，API 调用失败")
                print(f"错误: 无法获取 {SYMBOL} 的价格、交易量或指标，API 调用失败")
                send_telegram_message(f"❌ 程序错误: 无法获取 {SYMBOL} 的数据，API 调用失败")
                time.sleep(60)
                continue

            price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position, close, prev_close, avg_volume, prev_upper_shadow, prev_lower_shadow, prev_amplitude_percent, prev_position, prev_open = data
            
            # 判断是否为新K线结束（通过时间戳检查）
            current_ts = int(time.time() // 60 * 60)  # 当前分钟开始时间戳
            if current_ts > last_candle_ts:
                last_candle_ts = current_ts
                # K线结束时判断，使用上一根K线数据进行交易判断
                signal = None
                if rsi is not None and not pd.isna(ma['MA20']) and avg_volume is not None:
                    # 使用上一根K线的数据进行判断
                    # 计算实体大小
                    prev_body = abs(prev_close - prev_open)
                    # 做多信号：RSI超卖 + 收盘价在所有均线之下 + 成交量放大 + 长下影线（锤头线，表明底部反转） + 振幅足够
                    if (rsi < RSI_OVERSOLD and
                        prev_position == "在所有均线之下" and
                        volume > avg_volume and
                        prev_lower_shadow > SHADOW_RATIO * prev_body and
                        prev_lower_shadow > prev_upper_shadow and
                        prev_amplitude_percent > AMPLITUDE_THRESHOLD):
                        signal = "buy"
                    # 做空信号：RSI超买 + 收盘价在所有均线之上 + 成交量放大 + 长上影线（射击之星，表明顶部反转） + 振幅足够
                    elif (rsi > RSI_OVERBOUGHT and
                          prev_position == "在所有均线之上" and
                          volume > avg_volume and
                          prev_upper_shadow > SHADOW_RATIO * prev_body and
                          prev_upper_shadow > prev_lower_shadow and
                          prev_amplitude_percent > AMPLITUDE_THRESHOLD):
                        signal = "sell"
                
                rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
                ma20_str = f"{ma['MA20']:.2f}" if not pd.isna(ma['MA20']) else "N/A"
                
                # 推送判断结果
                if signal:
                    msg = f"⚠️ K线结束判断: 价格: {prev_close} | RSI: {rsi_str} | MA20: {ma20_str} | 成交量: {volume} (平均: {avg_volume}) | 上影线: {prev_upper_shadow} | 下影线: {prev_lower_shadow} | 振幅: {prev_amplitude_percent:.2f}% | 位置: {prev_position} | 信号: {signal.upper()}"
                    logging.info(msg)
                    print(msg)
                    send_telegram_message(msg)
            else:
                # 当前K线进行中，不进行交易判断
                signal = None

            # 检查价格是否在目标范围内（额外过滤）
            in_target_range = True  # 去除目标价格判断
            print(f"K线结束: {close}, signal={signal},last_signal={last_signal},当前持仓={current_position}, 目标范围内={in_target_range}, RSI={rsi_str}, MA20={ma20_str}, 成交量={volume} (平均: {avg_volume}),{AUTO_TRADE_ENABLED and signal and signal != last_signal and in_target_range}")
            if AUTO_TRADE_ENABLED and signal and signal != last_signal and in_target_range:
                if signal == "buy" and current_position != "long":
                    stop_loss = price * (1 - STOP_LOSS_PERCENT)
                    take_profit = price * (1 + TAKE_PROFIT_PERCENT)
                    order = place_order("buy", price, ORDER_SIZE, stop_loss, take_profit)
                    if order:
                        current_position = "long"
                        entry_price = price
                elif signal == "sell" and current_position != "short":
                    stop_loss = price * (1 + STOP_LOSS_PERCENT)
                    take_profit = price * (1 - TAKE_PROFIT_PERCENT)
                    order = place_order("sell", price, ORDER_SIZE, stop_loss, take_profit)
                    if order:
                        current_position = "short"
                        entry_price = price
                last_signal = signal

            # 止损/止盈检查
            if current_position == "long":
                if price <= stop_loss:
                    order = place_order("sell", price, ORDER_SIZE)
                    if order:
                        send_telegram_message(f"🛑 止损卖出: 价格={price}")
                        current_position = None
                elif price >= take_profit:
                    order = place_order("sell", price, ORDER_SIZE)
                    if order:
                        send_telegram_message(f"🎯 止盈卖出: 价格={price}")
                        current_position = None
            elif current_position == "short":
                if price >= stop_loss:
                    order = place_order("buy", price, ORDER_SIZE)
                    if order:
                        send_telegram_message(f"🛑 止损买入: 价格={price}")
                        current_position = None
                elif price <= take_profit:
                    order = place_order("buy", price, ORDER_SIZE)
                    if order:
                        send_telegram_message(f"🎯 止盈买入: 价格={price}")
                        current_position = None

            # 动态调整检查频率
            if signal and in_target_range:
                time.sleep(COOLDOWN)  # 交易后进入冷却
            else:
                time.sleep(CHECK_INTERVAL)  # 正常检查间隔

        except Exception as e:
            logging.error(f"程序错误: {e}")
            print(f"错误: {e}")
            send_telegram_message(f"❌ 程序错误: {e}")
            time.sleep(60)