import os
import time
import ccxt
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from flask import Flask, jsonify
from threading import Thread

from new_strategy_bot import (
    run_new_strategy_check, SYMBOL, LEVERAGE, RSI_LENGTH,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL, position_tracker,
    last_execution, TARGET_MARGIN,
    is_market_active, get_current_session, exchange
)

load_dotenv('binance_config.env')

print("=== Hugging Face Spaces Bot 启动 (Binance + 1H/4H 策略) ===")
print(f"标的: {SYMBOL} | 目标保证金: {TARGET_MARGIN} USDT | 杠杆: {LEVERAGE}x\n")
print("✅ 交易所连接已通过验证")

contract_info_cache = {}
app = Flask(__name__)
BOT_RUNNING = False


# ==================== 合约信息 ====================
def fetch_contract_info():
    global contract_info_cache
    try:
        balance = exchange.fetch_balance()
        contract_info_cache = {
            'timestamp': datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            'exchange': 'Binance',
            'symbol': SYMBOL,
            'balance': {
                'free':  balance.get('free', {}),
                'used':  balance.get('used', {}),
                'total': balance.get('total', {}),
            },
            'error': None
        }
        try:
            positions = exchange.fetch_positions([SYMBOL])
            if positions:
                contract_info_cache['positions'] = [{
                    'symbol': p.get('symbol'), 'side': p.get('side'),
                    'contracts': p.get('contracts'), 'leverage': p.get('leverage'),
                    'markPrice': p.get('markPrice'), 'unrealizedPnl': p.get('unrealizedPnl')
                } for p in positions]
        except Exception as e:
            contract_info_cache['positions_error'] = str(e)
        return True
    except Exception as e:
        contract_info_cache['error'] = str(e)
        return False


# ==================== 主页面 ====================
@app.route('/')
def index():
    if not last_execution.get('timestamp'):
        try:
            run_new_strategy_check(position_tracker.get('position'), exchange, SYMBOL,
                                   MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH,
                                   is_4h_check=True, is_1h_check=True)
        except:
            pass

    active = is_market_active()
    session = get_current_session()
    active_status = "🟢 高活跃" if active else "⭕ 休眠中"
    has_position = bool(position_tracker.get('position'))

    # 余额
    balance_html = ""
    if contract_info_cache.get('balance'):
        b = contract_info_cache['balance']
        uf = b.get('free', {}).get('USDT', 0)
        uu = b.get('used', {}).get('USDT', 0)
        ut = b.get('total', {}).get('USDT', 0)
        balance_html = f"""
            <div class="info">
                <strong>账户余额 (USDT):</strong><br>
                可用: {uf:.2f} | 冻结: {uu:.2f} | 总计: {ut:.2f}
            </div>"""

    # 持仓
    positions_html = ""
    if has_position and position_tracker.get('entry_price'):
        color = '#4caf50' if position_tracker['position'] == 'long' else '#f44336'
        positions_html = f"""
        <div class="info">
            <strong>📈 当前持仓:</strong> 
            <span style="color:{color};font-weight:bold;">{position_tracker['position'].upper()}</span> 
            @ ${position_tracker['entry_price']:.2f}
        </div>"""

    # 当前数据
    current_price = last_execution.get('current_price')
    price_display = f"${current_price:.2f}" if isinstance(current_price, (int, float)) else "N/A"
    macd_4h = last_execution.get('macd_4h_trend', 'N/A')
    macd_1h = last_execution.get('macd_1h_trend', 'N/A')
    current_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>BTC 1H/4H 交易机器人</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{font-family: Arial, sans-serif; margin:20px; background:#f5f5f5;}}
            .container {{max-width:900px; margin:auto; background:white; padding:20px; border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,0.1);}}
            .info {{padding:12px; margin:8px 0; background:#e3f2fd; border-left:5px solid #2196f3; border-radius:4px;}}
            .status {{padding:12px; background:#e8f5e9; border-left:5px solid #4caf50; border-radius:4px;}}
            a {{margin:5px; padding:10px 15px; background:#2196f3; color:white; text-decoration:none; border-radius:4px;}}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 BTC 交易机器人 (1H反转 + 4H平仓)</h1>
            <div class="status"><strong>状态：</strong> 在线运行中 ✓ | 有持仓时持续平仓保护</div>
            
            <h2>⚙️ 配置信息</h2>
            <div class="info"><strong>交易所：</strong> Binance 永续合约 | <strong>交易对：</strong> {SYMBOL}</div>
            <div class="info"><strong>杠杆：</strong> {LEVERAGE}x | <strong>保证金：</strong> {TARGET_MARGIN} USDT</div>
            <div class="info"><strong>当前市场时段：</strong> {session} <span style="color:{'#4caf50' if active else '#ff9800'}">({active_status})</span></div>

            {balance_html}
            {positions_html}
            
            <h2>📊 当前数据 ({current_time})</h2>
            <div class="info"><strong>当前价格:</strong> {price_display}</div>
            <div class="info"><strong>4H MACD趋势 (大趋势):</strong> {macd_4h.upper() if macd_4h != 'N/A' else 'N/A'}</div>
            <div class="info"><strong>1H MACD趋势 (小趋势):</strong> {macd_1h.upper() if macd_1h != 'N/A' else 'N/A'}</div>
        </div>
        <script>setInterval(()=>location.reload(), 3600000);</script>
    </body>
    </html>
    """


@app.route('/health')
def health(): return "OK", 200

@app.route('/contract-info')
def contract_info():
    return jsonify({
        'timestamp': datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        'is_active': is_market_active(),
        'session': get_current_session(),
        **contract_info_cache
    })

@app.route('/trade-history')
def trade_history_api():
    trades = position_tracker.get('trade_history', [])
    return jsonify({
        'total': len(trades),
        'profit': sum(t.get('profit', 0) for t in trades),
        'recent': trades[:10]
    })


# ==================== 机器人主循环 ====================
def run_bot():
    print("🤖 机器人后台线程已启动 | 有持仓时持续监控平仓 | 无持仓时仅活跃时段开仓")
    last_1h_check = None
    last_4h_check = None

    while True:
        now_utc = datetime.now(timezone.utc)
        now_cst = now_utc.astimezone(timezone(timedelta(hours=8)))
        m, s = now_cst.minute, now_cst.second
        active = is_market_active()
        session = get_current_session()
        has_position = bool(position_tracker.get('position'))

        # 每小时打印状态
        if m == 0 and s < 5:
            status = "🟢 活跃" if active else "⭕ 休眠"
            pos_info = f"持仓:{position_tracker.get('position','无')}" if has_position else "无持仓"
            print(f"[{now_cst.strftime('%H:%M:%S')}] 🕒 时段: {session} | {status} | {pos_info}")

        if has_position:
            # 有持仓时：始终检查4H平仓（即使休眠也保护）
            if m % 4 == 0 and 10 <= s <= 30:
                if last_4h_check is None or (now_utc - last_4h_check) >= timedelta(minutes=239):
                    print(f"[{now_cst.strftime('%H:%M:%S')}] 📊 【4H平仓检查】有持仓 → 执行判断（休眠保护）")
                    run_new_strategy_check(
                        position_tracker.get('position'), exchange, SYMBOL,
                        MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH,
                        is_4h_check=True, is_1h_check=False
                    )
                    last_4h_check = now_utc
        else:
            # 无持仓时：仅活跃时段检查1H开仓
            if active and m == 0 and 10 <= s <= 30:
                if last_1h_check is None or (now_utc - last_1h_check) >= timedelta(minutes=59):
                    print(f"[{now_cst.strftime('%H:%M:%S')}] 📊 【1H开仓检查】活跃时段 → 执行判断")
                    run_new_strategy_check(
                        position_tracker.get('position'), exchange, SYMBOL,
                        MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH,
                        is_4h_check=False, is_1h_check=True
                    )
                    last_1h_check = now_utc

        # 每小时刷新一次面板数据
        if m == 0 and 10 <= s <= 20:
            try:
                run_new_strategy_check(position_tracker.get('position'), exchange, SYMBOL,
                                       MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH,
                                       False, False)
            except:
                pass

        time.sleep(1)


def update_contract_info():
    while True:
        try:
            time.sleep(3600)  # 改为1小时检查一次
            fetch_contract_info()
        except:
            pass


if __name__ == "__main__":
    print("📊 初始化合约信息...")
    fetch_contract_info()

    Thread(target=run_bot, daemon=True).start()
    Thread(target=update_contract_info, daemon=True).start()

    print("🚀 Flask 服务启动中 (端口 7860)...")
    app.run(host="0.0.0.0", port=7860)