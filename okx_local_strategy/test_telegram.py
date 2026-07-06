#!/usr/bin/env python3
"""Telegram 推送测试脚本"""

import os
import requests
from dotenv import load_dotenv

load_dotenv('okx_config_v2.env')

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

print("=" * 50)
print("Telegram 推送测试")
print("=" * 50)
print(f"Bot Token: {TELEGRAM_BOT_TOKEN[:15]}...{TELEGRAM_BOT_TOKEN[-10:] if TELEGRAM_BOT_TOKEN else '未设置'}")
print(f"Chat ID: {TELEGRAM_CHAT_ID}")
print()

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❌ 请先在 okx_config_v2.env 中配置 Telegram 信息")
    exit(1)

# 测试消息
test_message = "🤖 **测试消息**\n\n" \
               "✅ Telegram 推送功能正常！\n" \
               "📅 时间: 2026-05-29 (测试)"

print("正在发送测试消息...")
try:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': test_message,
        'parse_mode': 'Markdown'
    }
    response = requests.post(url, data=payload)
    
    if response.status_code == 200:
        print("✅ Telegram 消息发送成功！")
        print("请检查你的 Telegram 聊天记录")
    else:
        print(f"❌ 发送失败，状态码: {response.status_code}")
        print(f"响应内容: {response.text}")
        
except Exception as e:
    print(f"❌ 发送异常: {e}")

print()
print("=" * 50)
