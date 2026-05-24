#!/usr/bin/env python3
import ccxt
import os
from dotenv import load_dotenv

load_dotenv('okx_config_v2.env')

exchange = ccxt.okx({
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET_KEY'),
    'password': os.getenv('OKX_PASSPHRASE'),
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

print('=== OKX 实际持仓 ===')

positions = exchange.fetch_positions()
has_pos = False
for pos in positions:
    contracts = float(pos.get('contracts', 0))
    if contracts != 0:
        has_pos = True
        symbol = pos.get('symbol', 'N/A')
        side = pos.get('side', 'N/A')
        entry = pos.get('entryPrice', 'N/A')
        print(f'{symbol}: {side} {contracts} @ {entry}')

if not has_pos:
    print('无持仓')
