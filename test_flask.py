import time
import requests
import logging
import pandas as pd
from okx import MarketData, Trade
import uuid

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
PRICE_ALERT = 121237.6  # 目标价格
PRICE_RANGE = 50  # 触发范围 (±50 USDT)
CHECK_INTERVAL = 5  # 正常检查间隔（秒）
COOLDOWN = 50  # 触发后的冷却时间（秒）
ORDER_SIZE = 0.1  # 下单数量
RSI_PERIOD = 14  # RSI 计算周期
MA_PERIODS = [20, 60, 120]  # MA 和 EMA 周期
CANDLE_LIMIT = max(MA_PERIODS)  # 获取足够 K 线数据
RSI_OVERBOUGHT = 70  # RSI 超买阈值
RSI_OVERSOLD = 30    # RSI 超卖阈值
STOP_LOSS_PERCENT = 0.02  # 止损百分比 (2%)
TAKE_PROFIT_PERCENT = 0.04  # 止盈百分比 (4%)

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

def calculate_ma_ema(data, periods):
    """计算 MA 和 EMA"""
    reversed_data = data[::-1]
    closes = pd.Series([float(candle[4]) for candle in reversed_data])
    ma = {f"MA{p}": closes.rolling(window=p).mean().iloc[-1] for p in periods}
    ema = {f"EMA{p}": closes.ewm(span=p, adjust=False).mean().iloc[-1] for p in periods}
    return ma, ema

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
                
                ma20_str = f"{ma['MA20']:.2f}" if not pd.isna(ma['MA20']) else "N/A"
                rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
                
                log_msg = (
                    f"成功获取价格: {price}, 交易量: {volume}, 上影线: {upper_shadow}, "
                    f"下影线: {lower_shadow}, 振幅: {amplitude_percent:.2f}%, "
                    f"RSI: {rsi_str}, MA20: {ma20_str}, 位置: {position}"
                )
                logging.info(log_msg)
                return price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position, close, prev_close
            else:
                logging.warning(f"K线 REST API 失败: {candles_data.get('msg')}")
                time.sleep(2)
                continue
        except Exception as e:
            logging.warning(f"获取数据失败 (尝试 {attempt + 1}/3): {e}")
            time.sleep(2)
    raise Exception(f"无法获取 {symbol} 的价格、交易量或指标，API 调用失败")

def place_order(side: str, price: float, size: float, stop_loss: float = None, take_profit: float = None):
    """下单，仅在成功后推送Telegram消息"""
    try:
        flag = "1" if IS_DEMO else "0"
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        pos_side = "long" if side == "buy" else "short"
        order_id = str(uuid.uuid4())
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
            error_msg = f"❌ 下单失败: {side.upper()}, 错误: {order.get('msg') or order['data'][0].get('sMsg')}"
            logging.error(error_msg)
            return None
    except Exception as e:
        error_msg = f"❌ 下单失败: {side.upper()}, 错误: {e}"
        logging.error(error_msg)
        send_telegram_message(error_msg)
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

    while True:
        try:
            price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position, close, prev_close = get_latest_price_and_indicators(SYMBOL)
            rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
            ma20_str = f"{ma['MA20']:.2f}" if not pd.isna(ma['MA20']) else "N/A"
            
            # 检查价格是否在目标范围内
            in_target_range = PRICE_ALERT - PRICE_RANGE <= price <= PRICE_ALERT + PRICE_RANGE
            signal = None
            if in_target_range and rsi is not None and not pd.isna(ma['MA20']):
                # 买入信号: 价格在目标区间，RSI超卖且价格上穿20周期MA
                if rsi < RSI_OVERSOLD and prev_close < ma['MA20'] and close > ma['MA20']:
                    signal = "buy"
                # 卖出信号: 价格在目标区间，RSI超买且价格下穿20周期MA
                elif rsi > RSI_OVERBOUGHT and prev_close > ma['MA20'] and close < ma['MA20']:
                    signal = "sell"

            if in_target_range:
                msg = f"⚠️ 价格进入目标区间 [{PRICE_ALERT - PRICE_RANGE}, {PRICE_ALERT + PRICE_RANGE}]，当前价: {price}"
                send_telegram_message(msg)
                logging.info(msg)

            if AUTO_TRADE_ENABLED and signal and signal != last_signal:
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

            # 输出当前状态
            msg = (
                f"当前价格: {price}\n"
                f"交易量: {volume} 合约\n"
                f"上影线: {upper_shadow}\n"
                f"下影线: {lower_shadow}\n"
                f"振幅: {amplitude_percent:.2f}%\n"
                f"RSI: {rsi_str}\n"
                f"MA20: {ma20_str}\n"
                f"位置: {position}\n"
                f"当前持仓: {current_position or '无'}\n"
                f"信号: {signal or '无'}"
            )
            logging.info(msg)
            print(msg)
            send_telegram_message(msg)

            # 动态调整检查频率
            if PRICE_ALERT - 1000 <= price <= PRICE_ALERT + 1000:
                time.sleep(2)  # 接近目标区间时加快检查
            elif signal and in_target_range:
                time.sleep(COOLDOWN)  # 交易后进入冷却
            else:
                time.sleep(CHECK_INTERVAL)  # 正常检查间隔

        except Exception as e:
            logging.error(f"程序错误: {e}")
            print(f"错误: {e}")
            send_telegram_message(f"❌ 程序错误: {e}")
            time.sleep(60)