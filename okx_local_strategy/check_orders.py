#!/usr/bin/env python3
"""查看历史订单详情"""

import os
import ccxt
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv('okx_config_v2.env')

exchange = ccxt.okx({
    'apiKey':    os.getenv('OKX_API_KEY'),
    'secret':    os.getenv('OKX_SECRET_KEY'),
    'password':  os.getenv('OKX_PASSPHRASE'),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',
    }
})

SYMBOL = 'ETH-USDT-SWAP'

print("=== 最近的订单 ===")

# 计算24小时前的时间戳
since = int((datetime.now() - timedelta(hours=24)).timestamp() * 1000)

orders = exchange.fetch_closed_orders(SYMBOL, since=since, limit=20)

for order in reversed(orders):
    side = order['side']
    type_ = order['type']
    status = order['status']
    amount = order['amount']
    price = order['price']
    filled = order['filled']
    average = order['average']
    datetime_ = order['datetime']
    
    print(f"{datetime_} | {side:5} | {status:10} | 数量: {filled:.4f} | 价格: {average:.2f}")
