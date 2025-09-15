import os
import time
import json
import base64
import hmac
import requests
from flask import Flask, request, jsonify

# 从环境变量读取 API key
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

# ⚠️ 推荐先用 OKX 模拟盘
OKX_BASE_URL = "https://www.okx.com"

app = Flask(__name__)

def okx_request(method, path, data=None):
    """签名并请求 OKX API"""
    ts = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
    body = json.dumps(data) if data else ""
    message = f"{ts}{method}{path}{body}"

    sign = base64.b64encode(
        hmac.new(OKX_SECRET_KEY.encode(), message.encode(), digestmod="sha256").digest()
    ).decode()

    headers = {
        "OK-ACCESS-KEY": OKX_API_KEY,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
        "Content-Type": "application/json"
    }

    url = OKX_BASE_URL + path
    print(f"🔗 请求 OKX: {url} data={data}")

    r = requests.request(method, url, headers=headers, data=body)
    print("📩 返回:", r.status_code, r.text)
    return r.json()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    print("✅ 收到 TradingView 信号:", data)

    # 简单校验
    if not data or "side" not in data:
        return jsonify({"error": "无效数据"}), 400

    # 调用 OKX 下单接口
    order_data = {
        "instId": data.get("instId", "BTC-USDT"),
        "tdMode": data.get("tdMode", "isolated"),
        "side": data["side"],
        "ordType": data.get("ordType", "market"),
        "sz": data.get("sz", "0.001")  # 下单数量
    }

    okx_response = okx_request("POST", "/api/v5/trade/order", order_data)

    return jsonify({"status": "ok", "okx": okx_response}), 200

if __name__ == '__main__':
    print("🚀 Flask 正在启动，监听 127.0.0.1:5000/webhook")
    app.run(host="127.0.0.1", port=5000, debug=True)
