from flask import Flask, request, jsonify
import threading
import time
import requests, os

app = Flask(__name__)

# ============ 环境变量配置 ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SECRET_KEY = os.getenv("SECRET_KEY")  # 安全密钥

# 支持多个 Space 地址（用逗号分隔）
HF_SPACE_URLS = os.getenv(
    "HF_SPACE_URLS",
    "https://tangjohnson-fl.hf.space/,ttps://tangjohnson-fl.hf.space/,https://tangjohnson-jj.hf.space/,https://tangjohnson-bit.hf.space/,"
).split(",")

INTERVAL = int(os.getenv("PING_INTERVAL", "900"))  # 默认15分钟

# ============ 后台保活函数 ============
def ping_spaces():
    while True:
        for url in HF_SPACE_URLS:
            url = url.strip()
            if not url:
                continue
            try:
                r = requests.get(url, timeout=10)
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Ping {url} -> {r.status_code}")
            except Exception as e:
                print(f"[ERROR] Ping {url} failed: {e}")
        time.sleep(INTERVAL)

# ============ Flask 路由 ============
@app.route("/")
def home():
    return "✅ Telegram Push API Running & HuggingFace Keep-Alive Active"

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
    # 启动后台线程执行多 Space 保活
    t = threading.Thread(target=ping_spaces, daemon=True)
    t.start()

    # 启动 Flask 服务
    app.run(host="0.0.0.0", port=10000)
