from flask import Flask, request, jsonify
import time
import requests, os

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SECRET_KEY = os.getenv("SECRET_KEY")  # 安全密钥

# ============ 配置区域 ============
HF_SPACE_URL = os.getenv("HF_SPACE_URL", "https://tangjohnson-jj.hf.space/")
INTERVAL = int(os.getenv("PING_INTERVAL", "900"))  # 默认10分钟

@app.route("/")
def home():
    return "✅ Telegram Push API Running"

@app.route("/send", methods=["POST"])
def send_message():
    data = request.get_json()
    key = data.get("key", "")
    text = data.get("text", "")

    if key != SECRET_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    if not text:
        return jsonify({"error": "Missing text"}), 400

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    res = requests.post(url, data={"chat_id": CHAT_ID, "text": text})
    while True:
        try:
            r = requests.get(HF_SPACE_URL, timeout=10)
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Ping {HF_SPACE_URL} -> {r.status_code}")
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(INTERVAL)  
    return jsonify(res.json())

if __name__ == "__main__":
    ping_space()
    app.run(host="0.0.0.0", port=10000)
