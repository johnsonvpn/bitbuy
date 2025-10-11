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

# 接口开关
ENABLE_TICKER_API = True  # 是否启用 Ticker API
ENABLE_CANDLES_API = True  # 是否启用 Candles API
ENABLE_TAKER_VOLUME_API = True  # 是否启用 Taker Volume API

IS_DEMO = True  # True=模拟盘，False=实盘
AUTO_TRADE_ENABLED = True  # True=自动下单，False=仅发送提醒
SYMBOL = "BTC-USDT-SWAP"  # 永续合约
CHECK_INTERVAL = 5  # 正常检查间隔（秒）
COOLDOWN = 50  # 触发后的冷却时间（秒）
ORDER_SIZE = 0.1  # 下单数量
MIN_ORDER_SIZE = 0.001  # 最小下单数量
RSI_PERIOD = 14  # RSI 计算周期
MA_PERIODS = [20, 60, 120]  # MA 和 EMA 周期
CANDLE_LIMIT = max(MA_PERIODS) + 10  # 多获取一些用于平均成交量
RSI_OVERBOUGHT = 80  # RSI 超买阈值
RSI_OVERSOLD = 20    # RSI 超卖阈值
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
    """获取最新价格、RSI、MA、EMA、均线位置、上一根K线的RSI及Taker B/S"""
    price = 0.0
    rsi = None
    prev_rsi = None
    ma = {}
    ema = {}
    position = "无有效数据"
    close = 0.0
    prev_close = 0.0
    prev_taker_buy = 0.0
    prev_taker_sell = 0.0
    candles_data = []

    for attempt in range(3):
        try:
            # 获取最新价格 (Ticker API)
            if ENABLE_TICKER_API:
                flag = "1" if IS_DEMO else "0"
                market = MarketData.MarketAPI(flag=flag)
                ticker_data = market.get_ticker(instId=symbol)
                if ticker_data.get("code") != "0":
                    logging.warning(f"Ticker API 失败: {ticker_data.get('msg')}")
                    time.sleep(2)
                    continue
                price = float(ticker_data["data"][0]["last"])
            else:
                logging.warning("Ticker API 已禁用，返回默认价格 0.0")
                price = 0.0

            # 获取K线数据 (Candles API)
            if ENABLE_CANDLES_API:
                url = f"https://www.okx.com/api/v5/market/history-candles?instId={symbol}&bar=1m&limit={CANDLE_LIMIT}"
                response = requests.get(url, timeout=5)
                candles_data_response = response.json()
                if candles_data_response.get("code") == "0" and candles_data_response.get("data"):
                    candles_data = candles_data_response["data"]
                    candle = candles_data[0]  # 当前K线
                    prev_candle = candles_data[1] if len(candles_data) > 1 else candle  # 上一根K线
                    
                    close = float(candle[4])
                    prev_close = float(prev_candle[4])
                    
                    # 计算当前K线的RSI
                    rsi = calculate_rsi(candles_data)
                    # 计算上一根K线的RSI
                    prev_rsi = calculate_rsi(candles_data[1:]) if len(candles_data) > 1 else None
                    ma, ema = calculate_ma_ema(candles_data, MA_PERIODS)
                    position = determine_position(close, ma, ema)
                else:
                    logging.warning(f"K线 API 失败: {candles_data_response.get('msg')}")
                    time.sleep(2)
                    continue
            else:
                logging.warning("Candles API 已禁用，返回默认 K 线数据")
                candles_data = []
                close = 0.0
                prev_close = 0.0
                rsi = None
                prev_rsi = None
                ma = {}
                ema = {}
                position = "无 K 线数据"

            # 获取Taker Volume数据 (Taker Volume API)
            if ENABLE_TAKER_VOLUME_API:
                taker_url = f"https://www.okx.com/api/v5/market/history-taker-volume?instId={symbol}&period=1m&limit=2"
                taker_response = requests.get(taker_url, timeout=5)
                taker_data = taker_response.json()
                if taker_data.get("code") == "0" and taker_data.get("data"):
                    prev_taker_buy = float(taker_data["data"][1][1]) if len(taker_data["data"]) > 1 else 0.0
                    prev_taker_sell = float(taker_data["data"][1][2]) if len(taker_data["data"]) > 1 else 0.0
                else:
                    prev_taker_buy = 0.0
                    prev_taker_sell = 0.0
                    logging.warning(f"Taker Volume API 失败: {taker_data.get('msg')}")
            else:
                logging.warning("Taker Volume API 已禁用，返回默认 Taker Buy/Sell 0.0")
                prev_taker_buy = 0.0
                prev_taker_sell = 0.0

            ma20_str = f"{ma['MA20']:.2f}" if ma.get('MA20') and not pd.isna(ma['MA20']) else "N/A"
            rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
            prev_rsi_str = f"{prev_rsi:.2f}" if prev_rsi is not None else "N/A"
            
            log_msg = (
                f"成功获取数据: 价格={price}, RSI={rsi_str}, 上一根K线RSI={prev_rsi_str}, MA20={ma20_str}, "
                f"位置={position}, 上一根K线Taker Buy={prev_taker_buy}, Taker Sell={prev_taker_sell}"
            )
            
            logging.info(log_msg)
            return price, rsi, prev_rsi, ma, ema, position, close, prev_close, prev_taker_buy, prev_taker_sell, candles_data

        except Exception as e:
            logging.warning(f"获取数据失败 (尝试 {attempt + 1}/3): {e}")
            time.sleep(2)
    logging.error("所有接口尝试失败，返回默认值")
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
    current_size = 0.0  # 当前持仓数量
    entry_price = 0.0  # 入场价格
    last_signal = None  # 上一次交易信号
    stop_loss = 0.0  # 止损价格
    take_profit = 0.0  # 止盈价格
    last_candle_ts = 0  # 上一次K线时间戳
    
    # RSI 记录变量
    overbought_recorded_rsi = None  # 用于做空信号的记录RSI (>80)
    oversold_recorded_rsi = None    # 用于做多信号的记录RSI (<20)
    short_sl_recorded_rsi = None    # 做空止损记录RSI (<30)
    long_sl_recorded_rsi = None     # 做多止损记录RSI (>70)

    while True:
        try:
            data = get_latest_price_and_indicators(SYMBOL)
            if data is None:
                logging.error(f"无法获取 {SYMBOL} 的价格或指标，API 调用失败")
                print(f"错误: 无法获取 {SYMBOL} 的价格或指标，API 调用失败")
                send_telegram_message(f"❌ 程序错误: 无法获取 {SYMBOL} 的数据，API 调用失败")
                time.sleep(60)
                continue

            price, rsi, prev_rsi, ma, ema, position, close, prev_close, prev_taker_buy, prev_taker_sell, candles_data = data

            # 判断是否为新K线结束
            current_ts = int(time.time() // 60 * 60)  # 当前分钟开始时间戳
            beijing_tz = timezone(timedelta(hours=8))
            last_candle_utc = datetime.fromtimestamp(last_candle_ts, tz=timezone.utc) if last_candle_ts > 0 else None
            last_candle_time_str = last_candle_utc.astimezone(beijing_tz).strftime('%Y-%m-%d %H:%M:%S') if last_candle_utc else "N/A"
            current_utc = datetime.fromtimestamp(current_ts, tz=timezone.utc)
            current_time_str = current_utc.astimezone(beijing_tz).strftime('%Y-%m-%d %H:%M:%S')

            signal = None
            if current_ts > last_candle_ts:
                # 新K线开始，基于上一根K线检查记录条件
                last_candle_ts = current_ts
                
                # 记录超买RSI用于做空
                if prev_rsi is not None and prev_rsi > RSI_OVERBOUGHT:
                    overbought_recorded_rsi = prev_rsi
                    print(f"记录超买RSI: {overbought_recorded_rsi:.2f} (用于做空)")
                    logging.info(f"记录超买RSI: {overbought_recorded_rsi:.2f} (用于做空)")
                
                # 记录超卖RSI用于做多
                if prev_rsi is not None and prev_rsi < RSI_OVERSOLD:
                    oversold_recorded_rsi = prev_rsi
                    print(f"记录超卖RSI: {oversold_recorded_rsi:.2f} (用于做多)")
                    logging.info(f"记录超卖RSI: {oversold_recorded_rsi:.2f} (用于做多)")
                
                # 记录做空止损RSI
                if current_position == "short" and rsi is not None and rsi < 30:
                    short_sl_recorded_rsi = rsi
                    print(f"记录做空止损RSI: {short_sl_recorded_rsi:.2f}")
                    logging.info(f"记录做空止损RSI: {short_sl_recorded_rsi:.2f}")
                
                # 记录做多止损RSI
                if current_position == "long" and rsi is not None and rsi > 70:
                    long_sl_recorded_rsi = rsi
                    print(f"记录做多止损RSI: {long_sl_recorded_rsi:.2f}")
                    logging.info(f"记录做多止损RSI: {long_sl_recorded_rsi:.2f}")
            
            else:
                # 在当前K线，检查信号条件
                if overbought_recorded_rsi is not None and rsi is not None and rsi < overbought_recorded_rsi and prev_taker_sell > prev_taker_buy:
                    signal = "sell"
                    msg = f"⚠️ 做空信号: RSI {rsi:.2f} < 记录 {overbought_recorded_rsi:.2f}, Taker Sell > Buy ({prev_taker_sell} > {prev_taker_buy})"
                    logging.info(msg)
                    print(msg)
                    send_telegram_message(msg)
                
                if oversold_recorded_rsi is not None and rsi is not None and rsi > oversold_recorded_rsi and prev_taker_sell < prev_taker_buy:
                    signal = "buy"
                    msg = f"⚠️ 做多信号: RSI {rsi:.2f} > 记录 {oversold_recorded_rsi:.2f}, Taker Buy > Sell ({prev_taker_buy} > {prev_taker_sell})"
                    logging.info(msg)
                    print(msg)
                    send_telegram_message(msg)
                
                # 检查止损条件
                if current_position == "short" and short_sl_recorded_rsi is not None and rsi is not None and rsi > short_sl_recorded_rsi:
                    # 止损平空
                    order_size = current_size
                    order = place_order("buy", price, order_size)
                    if order:
                        send_telegram_message(f"🛑 做空止损: RSI {rsi:.2f} > 记录 {short_sl_recorded_rsi:.2f}, 平空")
                        current_position = None
                        current_size = 0.0
                        short_sl_recorded_rsi = None
                
                if current_position == "long" and long_sl_recorded_rsi is not None and rsi is not None and rsi < long_sl_recorded_rsi:
                    # 止损平多
                    order_size = current_size
                    order = place_order("sell", price, order_size)
                    if order:
                        send_telegram_message(f"🛑 做多止损: RSI {rsi:.2f} < 记录 {long_sl_recorded_rsi:.2f}, 平多")
                        current_position = None
                        current_size = 0.0
                        long_sl_recorded_rsi = None

            # 修复格式化问题
            rsi_display = f"{rsi:.2f}" if rsi is not None else "N/A"
            prev_rsi_display = f"{prev_rsi:.2f}" if prev_rsi is not None else "N/A"
            print(f"当前时间: {current_time_str} | 上一K线时间: {last_candle_time_str} | 收盘价格: {close} | 位置: {position} | RSI: {rsi_display} | 上一根RSI: {prev_rsi_display} | 信号: {signal} | 持仓: {current_position}")

            # 交易逻辑
            if AUTO_TRADE_ENABLED and signal:
                order_size = max(ORDER_SIZE, MIN_ORDER_SIZE)

                if signal == "sell":
                    if current_position == "long":
                        # 平多
                        close_order = place_order("sell", price, current_size)
                        if close_order:
                            send_telegram_message(f"🛑 平多: 价格={price}")
                            current_position = None
                            current_size = 0.0
                        # 再做空
                        stop_loss = price * (1 + STOP_LOSS_PERCENT)
                        take_profit = price * (1 - TAKE_PROFIT_PERCENT)
                        open_order = place_order("sell", price, order_size, stop_loss, take_profit)
                        if open_order:
                            current_position = "short"
                            current_size = order_size
                            entry_price = price
                            last_signal = signal
                            overbought_recorded_rsi = None
                    elif current_position == "short":
                        # 加倍做空
                        add_size = current_size
                        stop_loss = price * (1 + STOP_LOSS_PERCENT)
                        take_profit = price * (1 - TAKE_PROFIT_PERCENT)
                        add_order = place_order("sell", price, add_size, stop_loss, take_profit)
                        if add_order:
                            current_size += add_size
                            entry_price = (entry_price * (current_size - add_size) + price * add_size) / current_size  # 更新平均入场价
                            last_signal = signal
                            overbought_recorded_rsi = None
                    else:
                        # 无持仓，直接做空
                        stop_loss = price * (1 + STOP_LOSS_PERCENT)
                        take_profit = price * (1 - TAKE_PROFIT_PERCENT)
                        open_order = place_order("sell", price, order_size, stop_loss, take_profit)
                        if open_order:
                            current_position = "short"
                            current_size = order_size
                            entry_price = price
                            last_signal = signal
                            overbought_recorded_rsi = None
                
                elif signal == "buy":
                    if current_position == "short":
                        # 平空
                        close_order = place_order("buy", price, current_size)
                        if close_order:
                            send_telegram_message(f"🛑 平空: 价格={price}")
                            current_position = None
                            current_size = 0.0
                        # 再做多
                        stop_loss = price * (1 - STOP_LOSS_PERCENT)
                        take_profit = price * (1 + TAKE_PROFIT_PERCENT)
                        open_order = place_order("buy", price, order_size, stop_loss, take_profit)
                        if open_order:
                            current_position = "long"
                            current_size = order_size
                            entry_price = price
                            last_signal = signal
                            oversold_recorded_rsi = None
                    elif current_position == "long":
                        # 加倍做多
                        add_size = current_size
                        stop_loss = price * (1 - STOP_LOSS_PERCENT)
                        take_profit = price * (1 + TAKE_PROFIT_PERCENT)
                        add_order = place_order("buy", price, add_size, stop_loss, take_profit)
                        if add_order:
                            current_size += add_size
                            entry_price = (entry_price * (current_size - add_size) + price * add_size) / current_size  # 更新平均入场价
                            last_signal = signal
                            oversold_recorded_rsi = None
                    else:
                        # 无持仓，直接做多
                        stop_loss = price * (1 - STOP_LOSS_PERCENT)
                        take_profit = price * (1 + TAKE_PROFIT_PERCENT)
                        open_order = place_order("buy", price, order_size, stop_loss, take_profit)
                        if open_order:
                            current_position = "long"
                            current_size = order_size
                            entry_price = price
                            last_signal = signal
                            oversold_recorded_rsi = None

            # 止盈检查
            if current_position == "long":
                if price >= take_profit:
                    order_size = current_size
                    order = place_order("sell", price, order_size)
                    if order:
                        send_telegram_message(f"🎯 止盈卖出: 价格={price}")
                        current_position = None
                        current_size = 0.0
                        last_signal = None
                        long_sl_recorded_rsi = None
            elif current_position == "short":
                if price <= take_profit:
                    order_size = current_size
                    order = place_order("buy", price, order_size)
                    if order:
                        send_telegram_message(f"🎯 止盈买入: 价格={price}")
                        current_position = None
                        current_size = 0.0
                        last_signal = None
                        short_sl_recorded_rsi = None

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