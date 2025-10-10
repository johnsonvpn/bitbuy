import time
import requests
import logging
import pandas as pd
from okx import MarketData

# ============ 配置区域 ============

# Telegram Bot 配置
BOT_TOKEN = "8239027160:AAGllh-w2_4mCI3B1oEPfQHgBeOiD6Zt3ZU"
CHAT_ID = 8024914547  # 请替换为你的 Telegram Chat ID

# OKX API 配置
IS_DEMO = True  # True=模拟盘，False=实盘
SYMBOL = "BTC-USDT"  # 现货符号
CHECK_INTERVAL = 60  # 检查间隔（秒）
BAR = "1m"  # K线时间框架（1分钟）
RSI_PERIOD = 14  # RSI 计算周期
MA_PERIODS = [20, 60, 120]  # MA 和 EMA 周期
CANDLE_LIMIT = max(MA_PERIODS)  # 获取足够 K 线数据

# 配置日志
logging.basicConfig(
    filename="price_monitor.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ============ 功能函数 ============

def send_telegram_message(message: str):
    """发送 Telegram 消息"""
    logging.debug(f"尝试发送 Telegram 消息: {message}")
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            logging.info("Telegram 消息发送成功")
            return True
        else:
            logging.error(f"Telegram 消息发送失败: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Telegram 消息发送错误: {e}")
        return False

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
    if not all_lines:  # 如果所有均线都是 NaN
        return "无有效均线"
    if all(close > line for line in all_lines):
        return "在所有均线之上"
    elif all(close < line for line in all_lines):
        return "在所有均线之下"
    else:
        return "在均线之间"

def get_latest_price_and_indicators(symbol: str) -> tuple:
    """获取最新价格、交易量、上下影线、振幅百分比、RSI、MA、EMA 和均线位置"""
    logging.debug(f"尝试获取 {symbol} 的最新价格、交易量、上下影线、振幅百分比、RSI 和均线")
    for attempt in range(3):
        try:
            # 获取最新价格
            flag = "1" if IS_DEMO else "0"
            market = MarketData.MarketAPI(flag=flag)
            ticker_data = market.get_ticker(instId=symbol)
            logging.debug(f"价格 API 返回: {ticker_data}")
            price = float(ticker_data["data"][0]["last"])
            
            # 获取 K 线数据
            url = f"https://www.okx.com/api/v5/market/history-candles?instId={symbol}&bar={BAR}&limit={CANDLE_LIMIT}"
            response = requests.get(url, timeout=5)
            candles_data = response.json()
            logging.debug(f"K线 REST API 返回: {candles_data}")
            if candles_data.get("code") == "0" and candles_data.get("data"):
                candle = candles_data["data"][0]
                open_price = float(candle[1])
                high = float(candle[2])
                low = float(candle[3])
                close = float(candle[4])
                volume = float(candle[5])
                
                # 计算上下影线
                upper_shadow = high - max(open_price, close)
                lower_shadow = min(open_price, close) - low
                
                # 计算振幅百分比
                amplitude_percent = (high - low) / low * 100 if low != 0 else 0.0
                
                # 计算 RSI
                rsi = calculate_rsi(candles_data["data"])
                
                # 计算 MA 和 EMA
                ma, ema = calculate_ma_ema(candles_data["data"], MA_PERIODS)
                
                # 判断均线位置
                position = determine_position(close, ma, ema)
                
                # 格式化 MA 和 EMA 值
                ma20_str = f"{ma['MA20']:.2f}" if not pd.isna(ma['MA20']) else "N/A"
                ma60_str = f"{ma['MA60']:.2f}" if not pd.isna(ma['MA60']) else "N/A"
                ma120_str = f"{ma['MA120']:.2f}" if not pd.isna(ma['MA120']) else "N/A"
                ema20_str = f"{ema['EMA20']:.2f}" if not pd.isna(ema['EMA20']) else "N/A"
                ema60_str = f"{ema['EMA60']:.2f}" if not pd.isna(ema['EMA60']) else "N/A"
                ema120_str = f"{ema['EMA120']:.2f}" if not pd.isna(ema['EMA120']) else "N/A"
                rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
                
                # 日志消息
                log_msg = (
                    f"成功获取价格: {price}, 交易量: {volume}, 上影线: {upper_shadow}, "
                    f"下影线: {lower_shadow}, 振幅: {amplitude_percent:.2f}%, "
                    f"RSI: {rsi_str}, "
                    f"MA20: {ma20_str}, MA60: {ma60_str}, MA120: {ma120_str}, "
                    f"EMA20: {ema20_str}, EMA60: {ema60_str}, EMA120: {ema120_str}, "
                    f"位置: {position}"
                )
                logging.info(log_msg)
                return price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position
            else:
                logging.warning(f"K线 REST API 失败: {candles_data.get('msg')}")
                time.sleep(2)
                continue
        except Exception as e:
            logging.warning(f"获取数据失败 (尝试 {attempt + 1}/3): {str(e)}")
            time.sleep(2)
    raise Exception(f"无法获取 {symbol} 的价格、交易量或指标，API 调用失败")

# ============ 主程序 ============

if __name__ == "__main__":
    logging.info("🚀 启动 OKX 价格、交易量和指标监控系统...")
    print("启动系统...")
    send_result = send_telegram_message("🤖 机器人已启动！开始监控 BTC/USDT 价格、交易量、上下影线、振幅、RSI 和均线。")
    logging.debug(f"启动消息发送结果: {send_result}")

    while True:
        try:
            price, volume, upper_shadow, lower_shadow, amplitude_percent, rsi, ma, ema, position = get_latest_price_and_indicators(SYMBOL)
            rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
            ma20_str = f"{ma['MA20']:.2f}" if not pd.isna(ma['MA20']) else "N/A"
            ma60_str = f"{ma['MA60']:.2f}" if not pd.isna(ma['MA60']) else "N/A"
            ma120_str = f"{ma['MA120']:.2f}" if not pd.isna(ma['MA120']) else "N/A"
            ema20_str = f"{ema['EMA20']:.2f}" if not pd.isna(ema['EMA20']) else "N/A"
            ema60_str = f"{ema['EMA60']:.2f}" if not pd.isna(ema['EMA60']) else "N/A"
            ema120_str = f"{ema['EMA120']:.2f}" if not pd.isna(ema['EMA120']) else "N/A"
            msg = (
                f"当前价格: {price}\n"
                f"交易量: {volume} 合约\n"
                f"上影线: {upper_shadow}\n"
                f"下影线: {lower_shadow}\n"
                f"振幅: {amplitude_percent:.2f}%\n"
                f"RSI: {rsi_str}\n"
                f"MA20: {ma20_str}\n"
                f"MA60: {ma60_str}\n"
                f"MA120: {ma120_str}\n"
                f"EMA20: {ema20_str}\n"
                f"EMA60: {ema60_str}\n"
                f"EMA120: {ema120_str}\n"
                f"位置: {position}"
            )
            logging.info(msg)
            print(msg)
            # send_telegram_message(msg)
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            logging.error(f"程序错误: {str(e)}")
            print(f"错误: {str(e)}")
            send_telegram_message(f"❌ 程序错误: {str(e)}")
            time.sleep(60)