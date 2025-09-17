import hmac
import base64
import json
import re
import requests
from flask import Flask, request, jsonify
from datetime import datetime, timezone

# ========= 配置 =========
OKX_API_KEY = "0a5d7703-c03b-4955-8ef5-8ce14ab327c9"
OKX_SECRET_KEY = "073A9B3817203635D4A126AFB94D1F82"
OKX_PASSPHRASE = "gamewell810DO*"
OKX_BASE_URL = "https://www.okx.com"

# ========= 工具函数 =========
def sign(message, secret_key):
    mac = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), digestmod="sha256")
    return base64.b64encode(mac.digest()).decode()

def place_order(instId, side, size):
    url = f"{OKX_BASE_URL}/api/v5/trade/order"
    # ✅ 使用 UTC ISO8601 毫秒格式
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    # 根据instId提取ccy参数
    ccy = instId.split("-")[0]  # 从交易对中提取货币，例如从 "BTC-USDT" 提取 "BTC"
    
    body = {
        "instId": instId,
        "tdMode": "cross",
        "side": side,
        "ordType": "market",
        "sz": str(size),
        "ccy": ccy
    }

    message = timestamp + "POST" + "/api/v5/trade/order" + json.dumps(body)
    signature = sign(message, OKX_SECRET_KEY)

    headers = {
        "OK-ACCESS-KEY": OKX_API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
        "Content-Type": "application/json",
        "x-simulated-trading": "1"   # ✅ 模拟盘，真实下单请去掉
    }

    print("\n========== OKX API Request ==========")
    print("➡️ URL:", url)
    print("➡️ Method: POST")
    print("➡️ Body:", json.dumps(body))
    print("➡️ Headers:", headers)

    resp = requests.post(url, headers=headers, data=json.dumps(body))
    print("⬅️ Response Status:", resp.status_code)
    print("⬅️ Response Body:", resp.text)
    print("=====================================\n")

    return resp.json()

# ========= Flask 应用 =========
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_data(as_text=True)
        print("📩 收到原始信号:", data)

        # ✅ 正则解析 TradingView 警报
        match = re.search(r"订单(\w+)@([\d\.]+)成交(\w+)", data)
        if not match:
            return jsonify({"error": "无法解析信号"}), 400

        side = match.group(1).lower()   # buy / sell
        size = match.group(2)
        raw_instId = match.group(3)
        # instId 映射逻辑
        if raw_instId.endswith(".P"):
            instId = raw_instId.replace("USDT.P", "-USDT-SWAP")
        else:
            instId = raw_instId.replace("USDT", "-USDT")

        print(f"✅ 解析结果: side={side}, size={size}, instId={instId}")

        # ✅ 执行下单
        result = place_order(instId, side, size)

        return jsonify(result)
    except Exception as e:
        print("❌ 处理请求时出错:", e)
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    print("🚀 Flask Webhook 已启动，监听 http://127.0.0.1:5000/webhook")
    app.run(host="0.0.0.0", port=5000, debug=True)