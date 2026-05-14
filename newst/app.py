import os
import time
import ccxt
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from flask import Flask, jsonify
from threading import Thread

from new_strategy_bot import (
    run_new_strategy_check,
    SYMBOL, LEVERAGE, RSI_LENGTH,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    position_tracker, last_execution,
    fetch_klines, calculate_indicators,
    get_macd_trend_30m, get_macd_trend_5m,
    TARGET_MARGIN,
)

load_dotenv('binance_config.env')

print("=== Hugging Face Spaces Bot 启动 (OKX) ===")
print(f"标的: {SYMBOL} | 目标保证金: {TARGET_MARGIN} USDT | 杠杆: {LEVERAGE}x\n")

exchange = ccxt.okx({
    'apiKey':   os.getenv('OKX_API_KEY'),
    'secret':   os.getenv('OKX_SECRET_KEY'),
    'password': os.getenv('OKX_PASSPHRASE'),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap'
    }
})

contract_info_cache = {}

app        = Flask(__name__)
BOT_RUNNING = False

# ==================== 合约信息缓存 ====================
def fetch_contract_info():
    global contract_info_cache
    try:
        balance = exchange.fetch_balance()
        contract_info_cache = {
            'timestamp': datetime.now(timezone.utc).astimezone(
                timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            'exchange': 'OKX',
            'symbol':   SYMBOL,
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
                    'symbol':          p.get('symbol'),
                    'side':            p.get('side'),
                    'contracts':       p.get('contracts'),
                    'contractSize':    p.get('contractSize'),
                    'leverage':        p.get('leverage'),
                    'collateral':      p.get('collateral'),
                    'percentage':      p.get('percentage'),
                    'markPrice':       p.get('markPrice'),
                    'liquidationPrice': p.get('liquidationPrice'),
                    'unrealizedPnl':   p.get('unrealizedPnl'),
                    'realizedPnl':     p.get('realizedPnl'),
                } for p in positions]
        except Exception as e:
            contract_info_cache['positions_error'] = str(e)

        print(f"✓ 合约信息已更新: {contract_info_cache['timestamp']}")
        return True
    except Exception as e:
        contract_info_cache['error'] = str(e)
        print(f"✗ 获取合约信息失败: {e}")
        return False

# ==================== 面板路由 ====================
@app.route('/')
def index():
    # 面板首次加载时拉取实时数据
    if not last_execution.get('timestamp'):
        try:
            print("[面板初始化] 开始获取实时数据...")
            ticker        = exchange.fetch_ticker(SYMBOL)
            current_price = float(ticker['last'])

            df30m = fetch_klines(exchange, SYMBOL, '30m', 120)
            df30m = calculate_indicators(df30m, '30m', MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH)
            macd_30m_trend = get_macd_trend_30m(df30m)

            df5m = fetch_klines(exchange, SYMBOL, '5m', 100)
            df5m = calculate_indicators(df5m, '5m', MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH)
            macd_5m_trend = get_macd_trend_5m(df5m)

            last_execution['timestamp']              = datetime.now(timezone.utc).astimezone(
                timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
            last_execution['current_price']          = current_price
            last_execution['macd_30m_trend']         = macd_30m_trend
            last_execution['macd_5m_trend']          = macd_5m_trend
            last_execution['last_5m_macd_direction'] = position_tracker.get('last_5m_macd_direction')
            print(f"[面板初始化] ✓ 成功: {last_execution['timestamp']}")
        except Exception as e:
            import traceback
            print(f"[面板初始化] ✗ 失败: {e}\n{traceback.format_exc()}")
            last_execution.setdefault('timestamp', datetime.now(timezone.utc).astimezone(
                timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"))

    # --- 余额 ---
    balance_html = ""
    if contract_info_cache.get('balance'):
        b = contract_info_cache['balance']
        uf = b.get('free',  {}).get('USDT', 0)
        uu = b.get('used',  {}).get('USDT', 0)
        ut = b.get('total', {}).get('USDT', 0)
        balance_html = f"""
            <div class="info">
                <strong>账户余额 (USDT):</strong><br>
                可用: {uf:.2f} | 冻结: {uu:.2f} | 总计: {ut:.2f}
            </div>"""

    # --- 持仓 ---
    positions_html = ""
    if position_tracker.get('position') and position_tracker.get('entry_price'):
        ep        = position_tracker['entry_price']
        direction = position_tracker['position'].upper()
        color     = '#4caf50' if position_tracker['position'] == 'long' else '#f44336'
        et_str    = (position_tracker['entry_time']
                     .astimezone(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
                     if position_tracker.get('entry_time') else 'N/A')
        entry_5m  = position_tracker.get('entry_5m_macd_direction', 'N/A')
        positions_html = f"""
        <div class="info">
            <strong>📈 当前持仓信息</strong><br>
            <div style='margin:10px 0;padding:10px;background:#f9f9f9;
                        border-left:3px solid {color};border-radius:3px;'>
                <strong>方向:</strong> <span style='color:{color};font-weight:bold;'>{direction}</span><br>
                <strong>开仓价:</strong> ${ep:.2f} @ {et_str}<br>
                <strong>开仓时5min方向:</strong> {entry_5m.upper() if entry_5m else 'N/A'}
                <span style='color:#888;font-size:12px;'>（29:30时与30min对比决定是否平仓）</span>
            </div>
        </div>"""
    elif contract_info_cache.get('positions'):
        positions_html = "<div class='info'><strong>当前头寸:</strong><br>"
        for p in contract_info_cache['positions']:
            side  = p.get('side', 'N/A')
            color = '#4caf50' if side == 'long' else '#f44336' if side == 'short' else '#999'
            positions_html += f"""
            <div style='margin:10px 0;padding:10px;background:#f9f9f9;
                        border-left:3px solid {color};border-radius:3px;'>
                <strong>方向:</strong>
                <span style='color:{color};font-weight:bold;'>{side.upper() if side else 'NONE'}</span><br>
                <strong>合约数量:</strong> {p.get('contracts','N/A')} |
                <strong>杠杆:</strong> {p.get('leverage','N/A')}x<br>
                <strong>标记价格:</strong> ${p.get('markPrice','N/A')} |
                <strong>未实现P&L:</strong> ${p.get('unrealizedPnl','N/A')}
            </div>"""
        positions_html += "</div>"
    elif contract_info_cache.get('positions_error'):
        positions_html = f"""
            <div class="info" style='background:#ffebee;border-left-color:#f44336;'>
                <strong>头寸信息获取失败:</strong> {contract_info_cache['positions_error']}
            </div>"""

    # --- 实时数据区 ---
    current_price_val = last_execution.get('current_price')
    macd_30m          = last_execution.get('macd_30m_trend')
    macd_5m           = last_execution.get('macd_5m_trend')
    last_5m           = last_execution.get('last_5m_macd_direction')
    reversal          = last_execution.get('reversal_detected', False)
    closed            = last_execution.get('close_triggered', False)
    current_sys_time  = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

    reversal_badge = '⚡ 是' if reversal else '➡️  否'
    closed_badge   = '⬇️  是' if closed  else '➡️  否'

    last_exec_html = f"""
    <h2>📊 当前数据 ({current_sys_time})</h2>
    <div class="info">
        <strong>当前价格:</strong>
        <span style='font-weight:bold;'>
            {'$'+f'{current_price_val:.2f}' if current_price_val else '获取中...'}
        </span>
    </div>
    <div class="info">
        <strong>30min MACD趋势:</strong>
        <span style='font-weight:bold;'>{macd_30m.upper() if macd_30m else 'NONE'}</span>
    </div>
    <div class="info">
        <strong>5min MACD趋势（当前）:</strong>
        <span style='font-weight:bold;'>{macd_5m.upper() if macd_5m else 'NONE'}</span>
    </div>
    <div class="info">
        <strong>5min上次记录方向:</strong>
        <span style='font-weight:bold;'>{last_5m.upper() if last_5m else 'NONE（等待首次记录）'}</span>
        <span style='color:#888;font-size:12px;'>（与当前5min对比判断反转）</span>
    </div>
    <div class="info">
        <strong>本次检查检测到反转:</strong> {reversal_badge} |
        <strong>本次检查触发平仓:</strong> {closed_badge}
    </div>"""

    # --- 错误 ---
    error_html = ""
    if contract_info_cache.get('error'):
        error_html = f"""
            <div class="info" style='background:#ffebee;border-left-color:#f44336;'>
                <strong>⚠️ 错误:</strong> {contract_info_cache['error']}
            </div>"""

    timestamp_info = contract_info_cache.get('timestamp', '未获取')

    # --- 交易历史 ---
    trade_history = position_tracker.get('trade_history', [])
    trade_history_html = ""
    if trade_history:
        trade_history_html = "<h2>📜 最近交易历史</h2>"
        for i, trade in enumerate(trade_history[:10], 1):
            profit_color = '#4caf50' if trade['profit'] >= 0 else '#f44336'
            dir_color = '#4caf50' if trade['direction'] == 'long' else '#f44336'
            
            trade_history_html += f"""
            <div class="trade-record" style="padding:12px;margin:8px 0;background:#f9f9f9;border-left:4px solid {profit_color};border-radius:4px;">
                <div style="font-weight:bold;margin-bottom:6px;">
                    <span style="color:{dir_color};">[{trade['direction'].upper()}]</span>
                    <span style="color:{profit_color};">{'💰 +' if trade['profit'] >= 0 else '💸 -'}${abs(trade['profit']):.2f}</span>
                    <span style="color:#888;font-size:12px;margin-left:10px;">#{i}</span>
                </div>
                <div style="font-size:13px;line-height:1.6;">
                    开仓: {trade['entry_time'][:19] if trade['entry_time'] else 'N/A'} @ ${trade['entry_price']:.2f}<br>
                    平仓: {trade['exit_time'][:19]} @ ${trade['exit_price']:.2f}<br>
                    张数: {trade['contracts']} | 原因: {trade['reason']}
                </div>
            </div>"""
    else:
        trade_history_html = """
        <h2>📜 最近交易历史</h2>
        <div class="info">暂无交易记录</div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>交易机器人监控面板</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
            .container {{ max-width: 900px; margin: 0 auto; background: white;
                          padding: 20px; border-radius: 8px;
                          box-shadow: 0 2px 4px rgba(0,0,0,.1); }}
            h1 {{ color: #333; }}
            h2 {{ color: #555; margin-top: 20px; border-bottom: 2px solid #2196f3;
                  padding-bottom: 10px; }}
            .status {{ padding: 10px; margin: 10px 0; border-radius: 4px;
                       background: #e8f5e9; border-left: 4px solid #4caf50; }}
            .info   {{ padding: 10px; margin: 10px 0;
                       background: #e3f2fd; border-left: 4px solid #2196f3; }}
            .link-section {{ margin-top: 20px; padding-top: 20px; border-top: 1px solid #ddd; }}
            .link-section a {{ display: inline-block; margin: 10px 10px 10px 0;
                               padding: 10px 15px; background: #2196f3; color: white;
                               text-decoration: none; border-radius: 4px; }}
            .link-section a:hover {{ background: #1976d2; }}
            .refresh-btn {{ background: #ff9800 !important; }}
            .refresh-btn:hover {{ background: #f57c00 !important; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 OKX 交易机器人监控面板</h1>
            <div class="status"><strong>状态：</strong> 在线运行中 ✓</div>

            <h2>⚙️ 配置信息</h2>
            <div class="info"><strong>交易所：</strong> OKX (永续合约)</div>
            <div class="info"><strong>交易对：</strong> {SYMBOL}</div>
            <div class="info"><strong>杠杆倍数：</strong> {LEVERAGE}x</div>
            <div class="info"><strong>目标保证金：</strong> {TARGET_MARGIN} USDT（合约价值: {TARGET_MARGIN * LEVERAGE} USDT）</div>
            <div class="info">
                <strong>策略逻辑：</strong>
                每5min的04:30检测5min MACD反转 → 无持仓则开仓；
                每30min的29:30判断30min MACD与开仓时5min方向是否不同 → 不同则平仓
            </div>
            <div class="info">
                <strong>启动时间：</strong>
                {datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")}
            </div>

            <h2>💰 账户信息
                <span style="font-size:12px;color:#999;">({timestamp_info})</span>
            </h2>
            {balance_html}
            {positions_html}
            {error_html}

            {last_exec_html}

            {trade_history_html}

            <div class="link-section">
                <h3>🔗 API 端点</h3>
                <a href="/health">健康检查</a>
                <a href="/contract-info">合约详情 (JSON)</a>
                <a href="/trade-history">交易历史 (JSON)</a>
                <a href="/" class="refresh-btn">刷新页面</a>
            </div>

        </div>
        <script>
            setInterval(function() {{ location.reload(); }}, 30000);
        </script>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return "OK", 200

@app.route('/contract-info')
def contract_info():
    try:
        balance = exchange.fetch_balance()
        info = {
            'timestamp': datetime.now(timezone.utc).astimezone(
                timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            'exchange': 'OKX',
            'symbol':   SYMBOL,
            'balance': {
                'free':  balance.get('free',  {}),
                'used':  balance.get('used',  {}),
                'total': balance.get('total', {}),
            },
            'last_execution': {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in last_execution.items()
            }
        }
        try:
            positions = exchange.fetch_positions([SYMBOL])
            if positions:
                info['positions'] = [{
                    'symbol':          p.get('symbol'),
                    'contracts':       p.get('contracts'),
                    'contractSize':    p.get('contractSize'),
                    'leverage':        p.get('leverage'),
                    'collateral':      p.get('collateral'),
                    'side':            p.get('side'),
                    'percentage':      p.get('percentage'),
                    'markPrice':       p.get('markPrice'),
                    'liquidationPrice': p.get('liquidationPrice'),
                    'unrealizedPnl':   p.get('unrealizedPnl'),
                    'realizedPnl':     p.get('realizedPnl'),
                    'timestamp':       p.get('timestamp'),
                } for p in positions]
        except Exception as e:
            info['positions_error'] = str(e)
        return jsonify(info), 200
    except Exception as e:
        return jsonify({
            'error':          str(e),
            'timestamp':      datetime.now(timezone.utc).astimezone(
                timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            'last_execution': dict(last_execution),
        }), 500

@app.route('/trade-history')
def trade_history_api():
    try:
        trade_history = position_tracker.get('trade_history', [])
        total_profit = sum(t['profit'] for t in trade_history)
        win_count = sum(1 for t in trade_history if t['profit'] >= 0)
        total_count = len(trade_history)
        
        return jsonify({
            'timestamp': datetime.now(timezone.utc).astimezone(
                timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            'total_trades': total_count,
            'total_profit': total_profit,
            'win_count': win_count,
            'win_rate': (win_count / total_count * 100) if total_count > 0 else 0,
            'trades': trade_history
        }), 200
    except Exception as e:
        return jsonify({
            'error': str(e),
            'timestamp': datetime.now(timezone.utc).astimezone(
                timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        }), 500

# ==================== 机器人主循环 ====================
def _closed_candle_ts(df, period_min: int, now_u):
    """
    返回df中最新已收盘K线的开盘时间戳。
    若最后一根已收盘则返回它；否则返回倒数第二根（已收盘）。
    """
    if len(df) < 2:
        return None
    latest_ts  = df['timestamp'].iloc[-1]
    close_time = latest_ts + timedelta(minutes=period_min)
    return latest_ts if close_time <= now_u else df['timestamp'].iloc[-2]


def run_bot():
    global BOT_RUNNING
    BOT_RUNNING = True
    print("机器人后台线程已启动")

    _5m_done_ts  = None    # 防止对同一根5min重复执行开仓
    _30m_done_ts = None   # 防止对同一根30min重复执行平仓

    while True:
        now_utc = datetime.now(timezone.utc)
        now_cst = now_utc.astimezone(timezone(timedelta(hours=8)))
        m, s    = now_cst.minute, now_cst.second

        # ── 5min开仓窗口：每个5min周期的第4:30-5:00秒（确保K线已收盘）─────────────
        if not position_tracker.get('position') and ((m % 5 == 4 and s >= 30) or (m % 5 == 0 and s <= 10)):
            if not _5m_done_ts or now_utc > _5m_done_ts + timedelta(minutes=4):
                print(f"[{now_cst.strftime('%H:%M:%S')}] 📊 检查5min开仓条件...")
                run_new_strategy_check(
                    position_tracker.get('position'), exchange, SYMBOL,
                    MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH,
                    is_29_30_check=False, is_04_30_check=True
                )
                _5m_done_ts = now_utc

        # ── 30min平仓窗口：30min柱收盘前最后5秒检查（29:55-30:00或59:55-60:00）────────
        # 30min柱: 00:00-00:30, 00:30-01:00, ... 在收盘前5秒检查，直接使用当前K线
        if position_tracker.get('position') and s >= 55 and (m % 30 == 29 or m % 30 == 59):
            if not _30m_done_ts or now_utc > _30m_done_ts + timedelta(minutes=29):
                print(f"[{now_cst.strftime('%H:%M:%S')}] 📊 检查30min平仓条件...")
                run_new_strategy_check(
                    position_tracker.get('position'), exchange, SYMBOL,
                    MACD_FAST, MACD_SLOW, MACD_SIGNAL, RSI_LENGTH,
                    is_29_30_check=True, is_04_30_check=False
                )
                _30m_done_ts = now_utc

        time.sleep(1)

def update_contract_info():
    print("合约信息更新线程已启动")
    while True:
        try:
            time.sleep(30)
            fetch_contract_info()
        except Exception as e:
            print(f"更新合约信息时出错: {e}")

# ==================== 入口 ====================
if __name__ == "__main__":
    print("\n📊 正在初始化合约信息...")
    fetch_contract_info()

    # 启动机器人线程
    BOT_THREAD = Thread(target=run_bot, daemon=True)
    BOT_THREAD.start()

    # 启动合约信息更新线程
    UPDATE_THREAD = Thread(target=update_contract_info, daemon=True)
    UPDATE_THREAD.start()

    print("📡 启动 Flask 应用 (0.0.0.0:7860)...\n")
    app.run(host="0.0.0.0", port=7860)
