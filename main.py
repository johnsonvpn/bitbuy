from flask import Flask, request, jsonify
import requests, os

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SECRET_KEY = os.getenv("SECRET_KEY")  # 安全密钥

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
    return jsonify(res.json())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
