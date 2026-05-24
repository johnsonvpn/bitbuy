#!/usr/bin/env python3
"""临时脚本：平仓ETH持仓"""

import os
import ccxt
from dotenv import load_dotenv

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

print("=== 获取当前持仓 ===")
positions = exchange.fetch_positions([SYMBOL])
if not positions:
    print("无持仓")
else:
    position = positions[0]
    side = position['side']
    quantity = abs(float(position['contracts']))
    print(f"当前持仓: {side} {quantity} @ {position['entryPrice']}")
    
    print(f"\n=== 执行平仓 ===")
    params = {'posSide': side}
    if side == 'long':
        order = exchange.create_order(SYMBOL, 'market', 'sell', quantity, None, params)
    else:
        order = exchange.create_order(SYMBOL, 'market', 'buy', quantity, None, params)
    
    print(f"平仓成功! 订单ID: {order['id']}")
