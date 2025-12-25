from flask import Flask, request, jsonify
import threading
import time
import requests
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

# ============ 环境变量配置 ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SECRET_KEY = os.getenv("SECRET_KEY")

HF_SPACE_URLS = [url.strip() for url in os.getenv(
    "HF_SPACE_URLS",
    "https://pine6-btc.hf.space/"
).split(",") if url.strip()]

INTERVAL = int(os.getenv("PING_INTERVAL", "900"))

# 线程池
executor = ThreadPoolExecutor(max_workers=len(HF_SPACE_URLS) or 1)

# ============ 后台保活函数 ============
def ping_single_space(url):
    try:
        r = requests.get(url, timeout=10)
        return url, r.status_code
    except requests.exceptions.Timeout:
        return url, "timeout"
    except Exception as e:
        return url, f"error: {e}"

def ping_spaces():
    while True:
        if not HF_SPACE_URLS:
            time.sleep(INTERVAL)
            continue

        futures = [executor.submit(ping_single_space, url) for url in HF_SPACE_URLS]
        for future in as_completed(futures, timeout=15):
            url, result = future.result()
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Ping {url} -> {result}")

        print(f"--- 休眠 {INTERVAL}s ---")
        time.sleep(INTERVAL)

# ============ Flask 路由 ============
@app.route("/")
def home():
    return "Telegram Push API Running & HF Keep-Alive Active"

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
    res = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    return jsonify(res.json())

# ============ 主程序入口 ============
if __name__ == "__main__":
    t = threading.Thread(target=ping_spaces, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=10000)
