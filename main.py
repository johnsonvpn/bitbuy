from flask import Flask, request, jsonify
import threading
import time
import requests, os

app = Flask(__name__)

# ============ 环境变量配置 ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SECRET_KEY = os.getenv("SECRET_KEY")  # 安全密钥
HF_SPACE_URL = os.getenv("HF_SPACE_URL", "https://tangjohnson-jj.hf.space/")
INTERVAL = int(os.getenv("PING_INTERVAL", "900"))  # 默认15分钟

# ============ 后台保活函数 ============
def ping_space():
    while True:
        try:
            r = requests.get(HF_SPACE_URL, timeout=10)
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Ping {HF_SPACE_URL} -> {r.status_code}")
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(INTERVAL)

# ============ Flask 路由 ============
@app.route("/")
def home():
    return "✅ Telegram Push API Running"

@app.route("/send", methods=["POST"])
def send_message():
    data = request.get_json(force=True)
    key = data.get("key", "")
    text = data.get("text", "")

    if key != SECRET_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    if not text:
        return jsonify({"error": "Missing text"}), 400

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    res = requests.post(url, data={"chat_id": CHAT_ID, "text": text})
    return jsonify(res.json())

# ============ 主程序入口 ============
if __name__ == "__main__":
    # 启动一个后台线程用于定时 ping Hugging Face
    t = threading.Thread(target=ping_space, daemon=True)
    t.start()

    # 启动 Flask 服务
    app.run(host="0.0.0.0", port=10000)
