

import time
import requests
import logging
from okx import MarketData, Trade

# ============ 配置区域 ============

# Telegram Bot 配置
BOT_TOKEN = "8239027160:AAGllh-w2_4mCI3B1oEPfQHgBeOiD6Zt3Z"
CHAT_ID = 8024914575  # 请替换为你的 Telegram Chat ID

# OKX API 配置
API_KEY = "c5788dfe-8ef0-4a07-812b-15c4c8f890b0"
SECRET_KEY = "B72E8E3BE0141966165B18DF9D3805E9"
PASS_PHRASE = "gamewell810DO*"

IS_DEMO = True  # True=模拟盘，False=实盘
AUTO_TRADE_ENABLED = True  # True=自动下单，False=仅发送提醒

SYMBOL = "BTC-USDT-SWAP"  # 永续合约
PRICE_ALERT = 121831.0 # 目标价格
PRICE_RANGE = 50  # 触发范围 (±500 USDT)
CHECK_INTERVAL = 5  # 正常检查间隔（秒）
COOLDOWN = 50  # 触发后的冷却时间（秒）
ORDER_SIZE = 10 # 下单数量

# 配置日志
logging.basicConfig(
    filename="price_monitor.log",
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

def get_latest_price(symbol: str) -> float:
    """获取最新价格，带重试机制"""
    for attempt in range(3):  # 最多重试 3 次
        try:
            market = MarketData.MarketAPI(flag="1" if IS_DEMO else "0")
            data = market.get_ticker(instId=symbol)
            price = float(data["data"][0]["last"])
            return price
        except Exception as e:
            logging.warning(f"获取价格失败 (尝试 {attempt + 1}/3): {e}")
            time.sleep(2)  # 失败后等待 2 秒再重试
    raise Exception("无法获取价格，API 调用失败")

def place_order(side: str):
    print(f"下33333")
    """下单"""
    try:
        print(f"下d址单: {side}")
        flag = "1" if IS_DEMO else "0"
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        order = trade.place_order(
            instId=SYMBOL,
            tdMode="cross",
            side=side,
            ordType="market",
            sz=str(ORDER_SIZE)
        )
        print(f"下单成功: {side}, 数量: {ORDER_SIZE}, 订单详情: {order}")
        return order
    except Exception as e:
        print(f"下单失败: {e}")
        raise

# ============ 主程序 ============

if __name__ == "__main__":
    logging.info("🚀 启动 OKX 自动交易监控系统...")
    send_telegram_message("🤖 机器人已启动！开始监控 BTC/USDT 价格。")

    while True:
        try:
            price = get_latest_price(SYMBOL)
            logging.info(f"当前价格: {price}")
            print(f"当前价格: {price}")

            # 检查价格是否在目标范围内
            if PRICE_ALERT - PRICE_RANGE <= price <= PRICE_ALERT + PRICE_RANGE:
                msg = f"⚠️ BTC 价格进入目标区间 [{PRICE_ALERT - PRICE_RANGE}, {PRICE_ALERT + PRICE_RANGE}]，当前价: {price}"
                send_telegram_message(msg)
                logging.info(msg)

                if AUTO_TRADE_ENABLED:
                    print(f"进入buytrading: {price}")
                    order = place_order("buy")
                    send_telegram_message(f"✅ 已执行市价买单: {order}")
                else:
                    send_telegram_message("💤 下单功能未开启，仅发送提醒。")

                # 触发后进入冷却
                logging.info(f"进入 {COOLDOWN} 秒冷却期")
                time.sleep(COOLDOWN)
            else:
                # 如果价格接近目标（±1000 USDT），加快检查频率
                if PRICE_ALERT - 1000 <= price <= PRICE_ALERT + 1000:
                    time.sleep(2)  # 接近目标时每 2 秒检查
                else:
                    time.sleep(CHECK_INTERVAL)  # 正常间隔

        except Exception as e:
            logging.error(f"程序错误: {e}")
            send_telegram_message(f"❌ 程序错误: {e}")
            time.sleep(60)  # 错误后等待 1 分钟再试
