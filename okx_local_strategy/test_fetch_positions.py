#!/usr/bin/env python3
"""测试策略2的持仓获取"""

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

print(f"=== 测试 fetch_positions(['{SYMBOL}']) ===")
positions = exchange.fetch_positions([SYMBOL])
print(f"返回的持仓数量: {len(positions)}")

for i, pos in enumerate(positions):
    print(f"\n持仓 {i+1}:")
    print(f"  symbol: {pos.get('symbol')}")
    print(f"  side: {pos.get('side')}")
    print(f"  contracts: {pos.get('contracts')}")
    print(f"  entryPrice: {pos.get('entryPrice')}")
    print(f"  info: {pos.get('info')}")

print("\n=== 使用 fetch_positions() 获取所有持仓 ===")
all_positions = exchange.fetch_positions()
print(f"返回的持仓数量: {len(all_positions)}")

for i, pos in enumerate(all_positions):
    contracts = float(pos.get('contracts', 0))
    if contracts != 0:
        print(f"  {pos.get('symbol')}: {pos.get('side')} {contracts} @ {pos.get('entryPrice')}")
