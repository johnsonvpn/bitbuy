#!/usr/bin/env python3
"""简单的 Telegram 测试"""

import os
import requests
from dotenv import load_dotenv

# 加载环境变量
dotenv_path = '/Users/johnsontang/work/bitbuy/okx_local_strategy/okx_config_v2.env'
load_dotenv(dotenv_path)

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

print(f"Bot Token: {BOT_TOKEN}")
print(f"Chat ID: {CHAT_ID}")

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
payload = {
    'chat_id': CHAT_ID,
    'text': '测试消息 - OKX策略机器人'
}

print(f"\n发送到: {url}")
response = requests.post(url, data=payload)
print(f"状态码: {response.status_code}")
print(f"响应: {response.text}")
