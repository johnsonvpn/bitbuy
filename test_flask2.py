import time
import json
import logging
import requests
import websocket
from okx import MarketData, Trade

# ============ 配置区域 ============

# Telegram Bot 配置
BOT_TOKEN = "8239027160:AAGllh-w2_4mCI3B1oEPfQHgBeOiD6Zt3Z"
CHAT_ID = 8024914575  # 请替换为您的 Telegram Chat ID

# OKX API 配置
API_KEY = "0a5d7703-c03b-4955-8ef5-8ce14ab327c9"
SECRET_KEY = "073A9B3817203635D4A126AFB94D1F82"
PASS_PHRASE = "gamewell810DO*"

IS_DEMO = True  # True=模拟盘，False=实盘
AUTO_TRADE_ENABLED = False  # True=自动下单，False=仅发送提醒

SYMBOL = "BTC-USDT-SWAP"  # 永续合约
PRICE_ALERT = 121870.2  # 目标价格
PRICE_RANGE = 500  # 触发范围 (±500 USDT)
COOLDOWN = 300  # 触发后的冷却时间（秒）
ORDER_SIZE = 0.001  # 下单数量

# WebSocket 端点
WEBSOCKET_URL = "wss://ws.okx.com:8443/ws/v5/public"

# 配置日志
logging.basicConfig(
    filename="price_monitor_ws.log",
    level=logging.DEBUG,  # 改为 DEBUG 以记录更多细节
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
            print(f"Telegram 错误: {response.text}")
        else:
            logging.info(f"Telegram 消息发送成功: {message}")
    except Exception as e:
        logging.error(f"Telegram 消息发送错误: {e}")
        print(f"Telegram 错误: {e}")

def place_order(side: str):
    """下单"""
    try:
        flag = "1" if IS_DEMO else "0"
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        order = trade.place_order(
            instId=SYMBOL,
            tdMode="cross",
            side=side,
            ordType="market",
            sz=str(ORDER_SIZE)
        )
        logging.info(f"下单成功: {side}, 数量: {ORDER_SIZE}, 订单详情: {order}")
        return order
    except Exception as e:
        logging.error(f"下单失败: {e}")
        print(f"下单失败: {e}")
        raise

# 可选: 动态阈值（布林带上轨）
"""
def get_bollinger_band(symbol, timeframe="1H", limit=20):
    try:
        market = MarketData.MarketAPI(flag="1" if IS_DEMO else "0")
        klines = market.get_candlesticks(instId=symbol, bar=timeframe, limit=str(limit))
        closes = [float(kline[4]) for kline in klines["data"]]
        sma = sum(closes) / len(closes)
        std = (sum((x - sma) ** 2 for x in closes) / len(closes)) ** 0.5
        upper_band = sma + 2 * std
        logging.info(f"布林带上轨: {upper_band}")
        return upper_band
    except Exception as e:
        logging.error(f"布林带计算错误: {e}")
        return PRICE_ALERT
"""

# WebSocket 回调函数
def on_message(ws, message):
    """处理 WebSocket 消息"""
    try:
        logging.debug(f"收到 WebSocket 消息: {message}")
        data = json.loads(message)
        if "data" in data and len(data["data"]) > 0:
            price = float(data["data"][0]["last"])
            logging.info(f"实时价格: {price}")
            print(f"实时价格: {price}")

            # 检查价格是否在目标范围内
            if PRICE_ALERT - PRICE_RANGE <= price <= PRICE_ALERT + PRICE_RANGE:
                msg = f"⚠️ BTC 价格进入目标区间 [{PRICE_ALERT - PRICE_RANGE}, {PRICE_ALERT + PRICE_RANGE}]，当前价: {price}"
                send_telegram_message(msg)
                logging.info(msg)

                if AUTO_TRADE_ENABLED:
                    order = place_order("buy")
                    send_telegram_message(f"✅ 已执行市价买单: {order}")
                else:
                    send_telegram_message("💤 下单功能未开启，仅发送提醒。")

                # 触发后进入冷却
                logging.info(f"进入 {COOLDOWN} 秒冷却期")
                time.sleep(COOLDOWN)

            # 可选: 每小时更新动态阈值
            # global PRICE_ALERT
            # if int(time.time()) % 3600 == 0:
            #     PRICE_ALERT = get_bollinger_band(SYMBOL)

        else:
            logging.debug(f"收到非价格数据: {data}")
    except Exception as e:
        logging.error(f"WebSocket 消息处理错误: {e}")
        print(f"WebSocket 消息错误: {e}")
        send_telegram_message(f"❌ WebSocket 错误: {e}")

def on_error(ws, error):
    """处理 WebSocket 错误"""
    logging.error(f"WebSocket 错误: {error}")
    print(f"WebSocket 错误: {error}")
    send_telegram_message(f"❌ WebSocket 错误: {error}")

def on_close(ws, close_status_code, close_msg):
    """处理 WebSocket 关闭"""
    logging.info(f"WebSocket 连接关闭: 状态码={close_status_code}, 原因={close_msg}")
    print(f"WebSocket 连接关闭: {close_status_code}, {close_msg}")
    send_telegram_message("❌ WebSocket 连接关闭，正在尝试重连...")

def on_open(ws):
    """WebSocket 连接建立后订阅 ticker"""
    logging.info("WebSocket 连接已建立")
    print("WebSocket 连接已建立")
    send_telegram_message("🤖 WebSocket 机器人已启动！开始监控 BTC/USDT 价格。")
    subscribe_msg = {
        "op": "subscribe",
        "args": [{"channel": "tickers", "instId": SYMBOL}]
    }
    ws.send(json.dumps(subscribe_msg))
    logging.debug(f"发送订阅消息: {subscribe_msg}")

# ============ 主程序 ============

if __name__ == "__main__":
    logging.debug("🚀 启动 OKX WebSocket 监控系统...")
    print("🚀 启动 OKX WebSocket 监控系统...")
    send_telegram_message("🤖 WebSocket 监控系统启动中...")

    # 测试 Telegram 连接
    send_telegram_message("🚀 脚本启动测试消息")

    while True:
        try:
            logging.debug("尝试连接 WebSocket...")
            ws = websocket.WebSocketApp(
                WEBSOCKET_URL,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            logging.error(f"WebSocket 主循环错误: {e}")
            print(f"WebSocket 主循环错误: {e}")
            send_telegram_message(f"❌ WebSocket 主循环错误: {e}")
            time.sleep(60)  # 错误后等待 1 分钟再重连