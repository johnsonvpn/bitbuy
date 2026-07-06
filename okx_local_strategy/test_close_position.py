#!/usr/bin/env python
# -*- coding: utf-8 -*-
import ccxt
from config import API_KEY, API_SECRET, PASSPHRASE

# 初始化交易所
exchange = ccxt.okx({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'password': PASSPHRASE,
    'enableRateLimit': True,
})

SYMBOL = 'ETH-USDT-SWAP'

print(f"=== 测试平仓 {SYMBOL} ===")

# 获取当前持仓
positions = exchange.fetch_positions([SYMBOL])
print(f"返回的持仓数量: {len(positions)}")

active_position = None
for pos in positions:
    contracts = float(pos.get('contracts', 0))
    if contracts != 0:
        active_position = pos
        break

if not active_position:
    print("无持仓可平")
    exit(0)

side = active_position['side']
quantity = abs(float(active_position['contracts']))
print(f"\n检测到持仓: {side} {quantity}")
print(f"entryPrice: {active_position['entryPrice']}")
print(f"posSide (info): {active_position['info'].get('posSide')}")

# 尝试几种不同的平仓方式
print("\n=== 尝试方式1: 使用 create_order 加 posSide ===")
try:
    params = {'posSide': active_position['info']['posSide']}
    if side == 'long':
        order = exchange.create_order(SYMBOL, 'market', 'sell', quantity, None, params)
    else:
        order = exchange.create_order(SYMBOL, 'market', 'buy', quantity, None, params)
    print(f"✅ 平仓成功: {order}")
except Exception as e:
    print(f"❌ 方式1失败: {e}")

print("\n=== 尝试方式2: 只指定 reduceOnly ===")
try:
    params = {'reduceOnly': True}
    if side == 'long':
        order = exchange.create_order(SYMBOL, 'market', 'sell', quantity, None, params)
    else:
        order = exchange.create_order(SYMBOL, 'market', 'buy', quantity, None, params)
    print(f"✅ 平仓成功: {order}")
except Exception as e:
    print(f"❌ 方式2失败: {e}")

print("\n=== 尝试方式3: 同时使用 posSide 和 reduceOnly ===")
try:
    params = {'posSide': active_position['info']['posSide'], 'reduceOnly': True}
    if side == 'long':
        order = exchange.create_order(SYMBOL, 'market', 'sell', quantity, None, params)
    else:
        order = exchange.create_order(SYMBOL, 'market', 'buy', quantity, None, params)
    print(f"✅ 平仓成功: {order}")
except Exception as e:
    print(f"❌ 方式3失败: {e}")
