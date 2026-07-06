#!/usr/bin/env python3
import os
import requests
from dotenv import load_dotenv

# 加载环境变量
load_dotenv('/Users/johnsontang/work/bitbuy/okx_local_strategy/okx_config_v2.env')

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

print("=" * 60)
print("Telegram 推送测试")
print("=" * 60)
print(f"Bot Token: {BOT_TOKEN}")
print(f"Chat ID: {CHAT_ID}")
print()

if not BOT_TOKEN or not CHAT_ID:
    print("❌ 请先配置 Telegram 信息！")
    exit(1)

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
payload = {
    'chat_id': CHAT_ID,
    'text': '🤖 测试消息\n\n✅ OKX策略机器人 - Telegram推送测试成功！\n\n时间: 2026-05-29'
}

print("正在发送消息...")
try:
    response = requests.post(url, data=payload, timeout=10)
    print(f"状态码: {response.status_code}")
    print(f"响应内容: {response.text}")
    
    if response.status_code == 200:
        print("\n✅ 消息发送成功！请检查你的 Telegram")
    else:
        print("\n❌ 消息发送失败")
except Exception as e:
    print(f"❌ 发送异常: {e}")

print("=" * 60)
