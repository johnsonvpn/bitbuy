import time
import hmac
import base64
import json
import requests
import hashlib
import re
from datetime import datetime
from flask import Flask, request, jsonify
import os

app = Flask(__name__)

OKX_API_KEY = os.getenv("OKX_API_KEY", "0a5d7703-c03b-4955-8ef5-8ce14ab327c9")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY", "073A9B3817203635D4A126AFB94D1F82")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "gamewell810DO*")
OKX_BASE_URL = "https://www.okx.com"

# ================= 签名函数 =================
def sign(message: str, secret_key: str) -> str:
    mac = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), digestmod="sha256")
    return base64.b64encode(mac.digest()).decode()

# ======== 获取符合OKX要求的时间戳 =========
def get_okx_timestamp():
    # OKX要求的时间戳格式：ISO 8601格式，例如：2020-12-08T09:08:57.715Z
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

# ======== 签名函数 =========
def get_okx_headers(method, path, body=""):
    timestamp = get_okx_timestamp()
    message = timestamp + method.upper() + path + body
    signature = sign(message, OKX_SECRET_KEY)
    
    headers = {
        "OK-ACCESS-KEY": OKX_API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
        "Content-Type": "application/json",
        "x-simulated-trading": "1"  # 模拟交易
    }
    return headers

# ================= 下单函数 =================
def place_order(side):
    path = "/api/v5/trade/order"
    order_data = {
        "instId": "BTC-USDT-SWAP",
        "tdMode": "cross",
        "ordType": "market",
        "sz": "1",
        "side": side.lower(),
        "posSide": "long" if side.lower() == "buy" else "short"
    }
    body = json.dumps(order_data)
    headers = get_okx_headers("POST", path, body)

    print("\n========== OKX API Request ==========")
    print("➡️ URL:", OKX_BASE_URL + path)
    print("➡️ Method: POST")
    print("➡️ Headers:", json.dumps(headers, indent=2))
    print("➡️ Body:", body)

    r = requests.post(OKX_BASE_URL + path, headers=headers, data=body)
    print("⬅️ Response Status:", r.status_code)
    print("⬅️ Response Body:", r.text)
    print("=====================================\n")
    
    return r

# ================= 提取交易信号 =================
def extract_trading_signal(text):
    """从文本中提取交易信号 (buy/sell)"""
    text_lower = text.lower()
    
    # 检查明确的买入/卖出关键词
    if 'buy' in text_lower:
        return 'buy'
    elif 'sell' in text_lower:
        return 'sell'
    
    # 如果没有明确的关键词，尝试使用正则表达式匹配
    buy_patterns = [r'做多', r'买入', r'long', r'多单']
    sell_patterns = [r'做空', r'卖出', r'short', r'空单']
    
    for pattern in buy_patterns:
        if re.search(pattern, text_lower):
            return 'buy'
            
    for pattern in sell_patterns:
        if re.search(pattern, text_lower):
            return 'sell'
    
    return None

# ================= Webhook 接口 =================
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        # 优先解析 JSON
        data = request.get_json(silent=True)
        
        # 如果不是 JSON，尝试解析原始文本
        if data is None:
            raw_data = request.data.decode("utf-8")
            print("📩 收到原始信号:", raw_data)
            
            # 尝试从原始文本中提取交易信号
            side = extract_trading_signal(raw_data)
            
            if side:
                data = {"side": side}
            else:
                # 检查是否是简单的 buy/sell 字符串
                if raw_data.lower() in ["buy", "sell"]:
                    data = {"side": raw_data.lower()}
                else:
                    return jsonify({"status": "error", "message": "无法识别的交易信号"}), 400
        else:
            print("✅ 收到 TradingView JSON 信号:", data)

        side = data.get("side", "").lower()
        if side in ["buy", "sell"]:
            okx_response = place_order(side)
            if okx_response.status_code == 200:
                return jsonify({"status": "ok", "okx_response": okx_response.json()})
            else:
                return jsonify({"status": "error", "message": f"OKX API 错误: {okx_response.text}"}), 500
        else:
            return jsonify({"status": "error", "message": "无效的 side 参数"}), 400
    except Exception as e:
        print("❌ 处理请求时出错:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)