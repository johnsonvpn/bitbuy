import time
import requests
import logging
import pandas as pd
import numpy as np
from okx import MarketData, Trade, Account
from flask import Flask, request, render_template_string, json
import os
import re
from datetime import datetime, timezone, timedelta
import traceback
from threading import Thread, Event



# ============ 【关键修改】混合监控配置 ============
class HybridMonitorConfig:
    """混合监控配置"""
    
    # K线级别检查 (稳定信号)
    KLINE_CHECKS = {
        "strategy_signal": True,      # 策略信号
        "take_profit": True,          # 止盈
        "trailing_stop": True,        # 移动止损
        "time_stop": True,            # 时间止损
    }
    
    # 高频检查 (紧急保护) - 每5秒
    REALTIME_CHECKS = {
        "emergency_stop": True,       # 紧急止损
        "flash_crash": True,          # 闪崩保护
        "extreme_profit": True,       # 极端盈利保护
    }
    
    # 高频检查参数
    REALTIME_INTERVAL = 5            # 5秒检查一次
    EMERGENCY_STOP_PCT = 3.0         # 紧急止损: -3%
    FLASH_CRASH_PCT = 5.0            # 闪崩: -5%瞬间止损
    EXTREME_PROFIT_PCT = 8.0         # 极端盈利: +8%立即止盈


# ============ 【新增】高频监控线程 ============
class RealtimeMonitor:
    """
    实时监控线程
    每5秒检查一次持仓，仅处理紧急情况
    """
    
    def __init__(self, api_key, secret_key, passphrase, flag, symbol):
        from okx import Account
        
        self.acc = Account.AccountAPI(
            api_key=api_key,
            api_secret_key=secret_key,
            passphrase=passphrase,
            flag=flag
        )
        self.symbol = symbol
        self.running = False
        self.thread = None
        self.stop_event = Event()
        
        # 回调函数 (由主线程设置)
        self.on_emergency_stop = None
        self.on_flash_crash = None
        self.on_extreme_profit = None
        
        # 统计
        self.check_count = 0
        self.last_check_time = None
        
    def start(self):
        """启动实时监控"""
        if self.running:
            return
        
        self.running = True
        self.stop_event.clear()
        self.thread = Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("🔴 实时监控线程已启动 (5秒/次)")
    
    def stop(self):
        """停止实时监控"""
        self.running = False
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
        logging.info("🔴 实时监控线程已停止")
    
    def _monitor_loop(self):
        """监控主循环"""
        while self.running and not self.stop_event.is_set():
            try:
                self._check_position()
                self.check_count += 1
                self.last_check_time = datetime.now()
                
                # 等待5秒 (可中断)
                self.stop_event.wait(timeout=HybridMonitorConfig.REALTIME_INTERVAL)
                
            except Exception as e:
                logging.error(f"实时监控异常: {e}")
                time.sleep(5)
    
    def _check_position(self):
        """检查持仓并处理紧急情况"""
        try:
            positions = self.acc.get_positions(instId=self.symbol)
            if positions.get("code") != "0":
                return
            
            pos_data = positions.get("data", [])
            if not pos_data:
                return
            
            pos = pos_data[0]
            pos_size = float(pos.get("pos", 0))
            if pos_size == 0:
                return
            
            # 计算盈亏
            pos_side = pos.get("posSide")
            upl_ratio = float(pos.get("uplRatio", 0)) * 100
            profit_pct = upl_ratio if pos_side == "long" else -upl_ratio
            
            # 【检查1】闪崩保护: 瞬间亏损 -5%
            if HybridMonitorConfig.REALTIME_CHECKS["flash_crash"]:
                if profit_pct <= -HybridMonitorConfig.FLASH_CRASH_PCT:
                    logging.critical(f"⚠️ 闪崩检测: {profit_pct:.2f}%")
                    if self.on_flash_crash:
                        self.on_flash_crash(profit_pct)
                    return
            
            # 【检查2】紧急止损: -3%
            if HybridMonitorConfig.REALTIME_CHECKS["emergency_stop"]:
                if profit_pct <= -HybridMonitorConfig.EMERGENCY_STOP_PCT:
                    logging.warning(f"⚠️ 紧急止损触发: {profit_pct:.2f}%")
                    if self.on_emergency_stop:
                        self.on_emergency_stop(profit_pct)
                    return
            
            # 【检查3】极端盈利保护: +8%立即止盈
            if HybridMonitorConfig.REALTIME_CHECKS["extreme_profit"]:
                if profit_pct >= HybridMonitorConfig.EXTREME_PROFIT_PCT:
                    logging.info(f"🎉 极端盈利: {profit_pct:.2f}%")
                    if self.on_extreme_profit:
                        self.on_extreme_profit(profit_pct)
                    return
            
        except Exception as e:
            logging.error(f"持仓检查异常: {e}")

# ============ 北京时间日志 ============
class BeijingFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp)
        beijing = dt.astimezone(timezone(timedelta(hours=8)))
        return beijing.timetuple()

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        if datefmt:
            return time.strftime(datefmt, ct)
        else:
            return time.strftime("%Y-%m-%d %H:%M:%S", ct) + f".{int(record.msecs):03d}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()]
)
for handler in logging.getLogger().handlers:
    handler.setFormatter(BeijingFormatter())

# ============ 配置 ============
IS_DEMO = False
DEFAULT_SYMBOL = "BTC-USDT-SWAP"
DEFAULT_BAR_INTERVAL = "1m"
DEFAULT_ORDER_SIZE = 0.01
RENDER_URL = "https://bitbuy-w8xw.onrender.com/send"
CONFIG_FILE = "/tmp/config_history.json"
STATE_FILE = "/tmp/bot_state.json"

app = Flask(__name__)

# 全局变量
SYMBOL = DEFAULT_SYMBOL
BAR_INTERVAL = DEFAULT_BAR_INTERVAL
ORDER_SIZE = DEFAULT_ORDER_SIZE
API_KEY = SECRET_KEY = PASS_PHRASE = BOT_TOKEN = CHAT_ID = ""
USER_STRATEGY_CODE = ""
CONVERTED_STRATEGY_CODE = ""
BOT_RUNNING = False
BOT_THREAD = None
GLOBAL_FLAG = "0"
_state = {}

# ============ 【新增】增强版风控配置 ============
class RiskConfig:
    """风险控制配置"""
    # 止损设置
    STOP_LOSS_PCT = 2.0          # 固定止损: -2%
    TRAILING_STOP_PCT = 1.0      # 移动止损: 从峰值回撤1%
    
    # 止盈设置 (多级止盈)
    TAKE_PROFIT_LEVELS = [
        {"profit_pct": 1.5, "close_pct": 30},   # 盈利1.5%时平30%
        {"profit_pct": 3.0, "close_pct": 50},   # 盈利3%时平50%
        {"profit_pct": 5.0, "close_pct": 100},  # 盈利5%时全平
    ]
    
    # 每日风控
    DAILY_PROFIT_TARGET = 3.0    # 日盈利目标: 3%
    MAX_DAILY_LOSS = 5.0         # 最大日亏损: -5%
    MAX_CONSECUTIVE_LOSSES = 3   # 最多连续亏损3次
    
    # 持仓时间控制
    MAX_HOLD_BARS = 20           # 最大持仓K线数


# ============ 【新增】增强版持仓监控 ============
class PositionMonitor:
    """实时持仓监控器"""
    
    def __init__(self, api_key, secret_key, passphrase, flag, symbol):
        self.acc = Account.AccountAPI(
            api_key=api_key, 
            api_secret_key=secret_key, 
            passphrase=passphrase, 
            flag=flag
        )
        self.symbol = symbol
        self.entry_price = None
        self.entry_time = None
        self.position_side = None
        self.position_size = 0
        self.peak_profit_pct = 0
        self.bars_held = 0
        
    def update(self):
        """更新持仓信息"""
        try:
            positions = self.acc.get_positions(instId=self.symbol)
            if positions.get("code") != "0":
                return None
                
            pos_data = positions.get("data", [])
            if not pos_data:
                self._reset()
                return None
            
            pos = pos_data[0]
            self.position_side = pos.get("posSide")
            self.position_size = float(pos.get("pos", 0))
            
            if self.position_size == 0:
                self._reset()
                return None
            
            # 首次开仓记录
            if self.entry_price is None:
                self.entry_price = float(pos.get("avgPx", 0))
                self.entry_time = datetime.now()
                self.bars_held = 0
            
            self.bars_held += 1
            
            # 计算收益
            mark_price = float(pos.get("markPx", 0))
            upl = float(pos.get("upl", 0))
            upl_ratio = float(pos.get("uplRatio", 0)) * 100
            
            # 根据多空方向调整盈利计算
            if self.position_side == "long":
                profit_pct = upl_ratio
            else:  # short
                profit_pct = -upl_ratio
            
            # 更新峰值
            if profit_pct > self.peak_profit_pct:
                self.peak_profit_pct = profit_pct
            
            return {
                "side": self.position_side,
                "size": self.position_size,
                "entry_price": self.entry_price,
                "mark_price": mark_price,
                "upl": upl,
                "profit_pct": profit_pct,
                "peak_profit_pct": self.peak_profit_pct,
                "bars_held": self.bars_held,
                "entry_time": self.entry_time
            }
            
        except Exception as e:
            logging.error(f"持仓监控异常: {e}")
            return None
    
    def _reset(self):
        """重置监控状态"""
        self.entry_price = None
        self.entry_time = None
        self.position_side = None
        self.position_size = 0
        self.peak_profit_pct = 0
        self.bars_held = 0


# ============ 【新增】增强版风控引擎 ============
class RiskManager:
    """风险管理引擎"""
    
    def __init__(self):
        self.daily_trades = []
        self.consecutive_losses = 0
        self.daily_initial_balance = 0
        self.daily_profit_pct = 0
        self.last_trade_date = None
        self.stopped_trading = False
        
    def check_stop_loss(self, position_info):
        """检查止损条件"""
        if not position_info:
            return False, None
        
        profit_pct = position_info["profit_pct"]
        peak_pct = position_info["peak_profit_pct"]
        
        # 1. 固定止损: -2%
        if profit_pct <= -RiskConfig.STOP_LOSS_PCT:
            return True, f"触发固定止损: {profit_pct:.2f}% <= -{RiskConfig.STOP_LOSS_PCT}%"
        
        # 2. 移动止损: 从峰值回撤1%
        if peak_pct > 1.0:  # 只有盈利后才启用移动止损
            drawdown = peak_pct - profit_pct
            if drawdown >= RiskConfig.TRAILING_STOP_PCT:
                return True, f"触发移动止损: 从峰值{peak_pct:.2f}%回撤{drawdown:.2f}%"
        
        # 3. 持仓时间过长
        if position_info["bars_held"] >= RiskConfig.MAX_HOLD_BARS:
            return True, f"持仓时间过长: {position_info['bars_held']}根K线"
        
        return False, None
    
    def check_take_profit(self, position_info):
        """检查止盈条件 (多级止盈)"""
        if not position_info:
            return False, 0, None
        
        profit_pct = position_info["profit_pct"]
        
        for level in RiskConfig.TAKE_PROFIT_LEVELS:
            if profit_pct >= level["profit_pct"]:
                close_ratio = level["close_pct"] / 100
                msg = f"触发{level['profit_pct']}%止盈，平仓{level['close_pct']}%"
                return True, close_ratio, msg
        
        return False, 0, None
    
    def check_daily_limits(self, current_balance):
        """检查每日限制"""
        if self.daily_initial_balance == 0:
            return False, None
        
        self.daily_profit_pct = (current_balance - self.daily_initial_balance) / self.daily_initial_balance * 100
        
        # 达到日盈利目标
        if self.daily_profit_pct >= RiskConfig.DAILY_PROFIT_TARGET:
            self.stopped_trading = True
            return True, f"达到日盈利目标: {self.daily_profit_pct:.2f}% >= {RiskConfig.DAILY_PROFIT_TARGET}%"
        
        # 达到最大日亏损
        if self.daily_profit_pct <= -RiskConfig.MAX_DAILY_LOSS:
            self.stopped_trading = True
            return True, f"达到最大日亏损: {self.daily_profit_pct:.2f}% <= -{RiskConfig.MAX_DAILY_LOSS}%"
        
        # 连续亏损次数过多
        if self.consecutive_losses >= RiskConfig.MAX_CONSECUTIVE_LOSSES:
            self.stopped_trading = True
            return True, f"连续亏损{self.consecutive_losses}次，暂停交易"
        
        return False, None
    
    def record_trade(self, profit_pct):
        """记录交易结果"""
        self.daily_trades.append({
            "time": datetime.now(),
            "profit_pct": profit_pct
        })
        
        if profit_pct < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
    
    def new_day_reset(self, initial_balance):
        """新一天重置"""
        today = datetime.now().date()
        if self.last_trade_date != today:
            self.last_trade_date = today
            self.daily_initial_balance = initial_balance
            self.daily_trades = []
            self.consecutive_losses = 0
            self.stopped_trading = False
            self.daily_profit_pct = 0
            return True
        return False


# ============ 【新增】增强版报告生成 ============
def generate_position_report(position_info):
    """生成持仓报告"""
    if not position_info:
        return "当前无持仓"
    
    side_cn = "多头" if position_info["side"] == "long" else "空头"
    hold_time = (datetime.now() - position_info["entry_time"]).total_seconds() / 60
    
    report = f"""
╔══════════════════════════════════════╗
║          实时持仓监控报告             ║
╠══════════════════════════════════════╣
║ 持仓方向: {side_cn:>8}                ║
║ 持仓数量: {position_info['size']:>8.4f} 张          ║
║ 开仓价格: {position_info['entry_price']:>12.2f}        ║
║ 标记价格: {position_info['mark_price']:>12.2f}        ║
╠══════════════════════════════════════╣
║ 未实现盈亏: {position_info['upl']:>+10.4f} USDT    ║
║ 盈亏比例:   {position_info['profit_pct']:>+10.2f}%        ║
║ 峰值盈利:   {position_info['peak_profit_pct']:>+10.2f}%        ║
╠══════════════════════════════════════╣
║ 持仓时长: {hold_time:>6.0f} 分钟            ║
║ K线数量: {position_info['bars_held']:>6} 根              ║
╚══════════════════════════════════════╝
"""
    return report


def generate_daily_report(risk_manager):
    """生成每日报告"""
    report = f"""
╔══════════════════════════════════════╗
║          每日交易统计报告             ║
╠══════════════════════════════════════╣
║ 交易次数: {len(risk_manager.daily_trades):>6}              ║
║ 当日盈亏: {risk_manager.daily_profit_pct:>+10.2f}%        ║
║ 连续亏损: {risk_manager.consecutive_losses:>6} 次              ║
╠══════════════════════════════════════╣
║ 盈利目标: {RiskConfig.DAILY_PROFIT_TARGET:>6.1f}%              ║
║ 最大亏损: {RiskConfig.MAX_DAILY_LOSS:>6.1f}%              ║
╚══════════════════════════════════════╝
"""
    return report


def send_enhanced_telegram(position_info, risk_manager):
    """发送增强版Telegram通知"""
    
    if not position_info:
        msg = "📊 <b>当前无持仓</b>"
    else:
        side_emoji = "🟢" if position_info["side"] == "long" else "🔴"
        profit_emoji = "📈" if position_info["profit_pct"] > 0 else "📉"
        
        msg = f"""
{side_emoji} <b>持仓监控</b>
<b>方向:</b> {position_info['side'].upper()}
<b>数量:</b> {position_info['size']:.4f} 张
<b>开仓价:</b> {position_info['entry_price']:.2f}
<b>标记价:</b> {position_info['mark_price']:.2f}
{profit_emoji} <b>盈亏:</b> {position_info['profit_pct']:+.2f}%
<b>峰值:</b> {position_info['peak_profit_pct']:+.2f}%
<b>持仓:</b> {position_info['bars_held']} 根K线
💰 <b>今日盈亏:</b> {risk_manager.daily_profit_pct:+.2f}%
📊 <b>交易次数:</b> {len(risk_manager.daily_trades)}
"""
    
    send_telegram_message(msg)


# ============ 【新增】部分平仓功能 ============
def partial_close_position(close_ratio):
    """
    部分平仓
    close_ratio: 平仓比例 (0-1)
    """
    flag = GLOBAL_FLAG
    try:
        acc = Account.AccountAPI(
            api_key=API_KEY, 
            api_secret_key=SECRET_KEY, 
            passphrase=PASS_PHRASE, 
            flag=flag
        )
        
        positions = acc.get_positions(instId=SYMBOL)
        if positions.get("code") != "0":
            return False
        
        pos_data = positions.get("data", [])
        if not pos_data:
            return False
        
        pos = pos_data[0]
        pos_side = pos.get("posSide")
        total_size = float(pos.get("pos", 0))
        close_size = total_size * close_ratio
        
        # 使用市价单部分平仓
        trade = Trade.TradeAPI(
            api_key=API_KEY, 
            api_secret_key=SECRET_KEY, 
            passphrase=PASS_PHRASE, 
            flag=flag
        )
        
        close_side = "sell" if pos_side == "long" else "buy"
        
        order = trade.place_order(
            instId=SYMBOL,
            tdMode="cross",
            side=close_side,
            posSide=pos_side,
            ordType="market",
            sz=str(close_size)
        )
        
        if order.get("code") == "0":
            logging.info(f"部分平仓成功: {close_ratio*100:.0f}% ({close_size:.4f}张)")
            send_telegram_message(f"✅ 部分平仓成功\n平仓比例: {close_ratio*100:.0f}%\n平仓数量: {close_size:.4f}张")
            return True
        else:
            logging.error(f"部分平仓失败: {order.get('msg')}")
            return False
            
    except Exception as e:
        logging.error(f"部分平仓异常: {e}")
        return False


# ============ 配置历史 ============
def load_config_history():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        return []
    except:
        return []

def save_config_history(api_key, secret_key, pass_phrase, bot_token, chat_id):
    configs = load_config_history()
    new_config = {
        "api_key": api_key[-4:] if api_key else "",
        "secret_key": secret_key[-4:] if secret_key else "",
        "pass_phrase": pass_phrase[-4:] if pass_phrase else "",
        "bot_token": bot_token[-4:] if bot_token else "",
        "chat_id": chat_id,
        "full_config": {"api_key": api_key, "secret_key": secret_key, "pass_phrase": pass_phrase, "bot_token": bot_token, "chat_id": chat_id}
    }
    configs = [c for c in configs if c["full_config"] != new_config["full_config"]]
    configs.append(new_config)
    configs = configs[-5:]
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(configs, f)
    except Exception as e:
        logging.warning(f"保存配置失败: {e}")

# ============ 状态持久化 ============
def save_bot_state():
    state = {
        "running": BOT_RUNNING,
        "symbol": SYMBOL,
        "bar_interval": BAR_INTERVAL,
        "order_size": ORDER_SIZE,
        "is_demo": IS_DEMO,
        "api_key_last4": API_KEY[-4:] if API_KEY else "",
        "secret_key_last4": SECRET_KEY[-4:] if SECRET_KEY else "",
        "pass_phrase_last4": PASS_PHRASE[-4:] if PASS_PHRASE else "",
        "bot_token_last4": BOT_TOKEN[-4:] if BOT_TOKEN else "",
        "chat_id": CHAT_ID,
        "user_strategy_code": USER_STRATEGY_CODE,
        "converted_strategy_code": CONVERTED_STRATEGY_CODE,
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
        logging.info("状态已保存")
    except Exception as e:
        logging.warning(f"保存运行状态失败: {e}")

def load_bot_state():
    global BOT_RUNNING, SYMBOL, BAR_INTERVAL, ORDER_SIZE, IS_DEMO, GLOBAL_FLAG
    global API_KEY, SECRET_KEY, PASS_PHRASE, BOT_TOKEN, CHAT_ID, USER_STRATEGY_CODE, CONVERTED_STRATEGY_CODE

    if not os.path.exists(STATE_FILE):
        return False

    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)

        if not state.get("running", False):
            return False

        BOT_RUNNING = True
        SYMBOL = state["symbol"]
        BAR_INTERVAL = state["bar_interval"]
        ORDER_SIZE = state["order_size"]
        IS_DEMO = state["is_demo"]
        GLOBAL_FLAG = "1" if IS_DEMO else "0"

        API_KEY = "****" + state["api_key_last4"] if state.get("api_key_last4") else ""
        SECRET_KEY = "****" + state["secret_key_last4"] if state.get("secret_key_last4") else ""
        PASS_PHRASE = "****" + state["pass_phrase_last4"] if state.get("pass_phrase_last4") else ""
        BOT_TOKEN = "****" + state["bot_token_last4"] if state.get("bot_token_last4") else ""
        CHAT_ID = state["chat_id"]
        USER_STRATEGY_CODE = state.get("user_strategy_code", "")
        CONVERTED_STRATEGY_CODE = state.get("converted_strategy_code", "")

        logging.info("恢复机器人运行状态")
        return True
    except Exception as e:
        logging.warning(f"加载运行状态失败: {e}")
        try:
            os.remove(STATE_FILE)
        except:
            pass
    return False

# ============ 非阻塞通知 ============
def send_telegram_message(message: str):
    if not SECRET_KEY or not message.strip():
        return
    data = {"key": SECRET_KEY, "text": message[:4000]}
    def _send():
        try:
            requests.post(RENDER_URL, json=data, timeout=5)
        except:
            pass
    Thread(target=_send, daemon=True).start()

# ============ 获取账户余额 ============
def get_account_balance():
    flag = GLOBAL_FLAG
    try:
        acc = Account.AccountAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        balance = acc.get_account_balance(ccy="USDT")
        if balance.get("code") == "0" and balance.get("data") and balance["data"]:
            details = balance["data"][0].get("details", [])
            if details:
                usdt_detail = next((item for item in details if item.get("ccy") == "USDT"), None)
                if usdt_detail:
                    cash_bal = usdt_detail.get("availBal", usdt_detail.get("cashBal", "0"))
                    return float(cash_bal)
        err_msg = balance.get("msg", "未知错误")
        err_code = balance.get("code", "未知")
        logging.warning(f"获取余额失败 (code: {err_code}): {err_msg}")
    except Exception as e:
        logging.error(f"获取余额异常: {e}")
    return None

# ============ OKX 函数 ============
def get_latest_price_and_indicators(symbol: str, bar: str, max_retries=5):
    flag = GLOBAL_FLAG
    for attempt in range(max_retries):
        try:
            market = MarketData.MarketAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
            ticker = market.get_ticker(instId=symbol)
            if ticker.get("code") != "0":
                logging.warning(f"API 响应错误 (尝试 {attempt+1}): {ticker.get('msg', '未知')}")
                time.sleep(2 ** attempt)
                continue
            price = float(ticker["data"][0]["last"])

            hist = market.get_history_candlesticks(instId=symbol, bar=bar, limit="300")
            if hist.get("code") != "0" or not hist.get("data"):
                logging.warning(f"K线数据错误 (尝试 {attempt+1}): {hist.get('msg', '无数据')}")
                time.sleep(2 ** attempt)
                continue

            candles = hist["data"]
            logging.info(f"数据获取成功: 价格={price}, K线数={len(candles)}")
            return {"price": price, "candles": candles}

        except Exception as e:
            logging.error(f"获取数据异常 (尝试 {attempt+1}): {e}")
        
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt + np.random.uniform(0, 1)
            logging.info(f"重试等待 {wait_time:.1f}s...")
            time.sleep(wait_time)
    
    send_telegram_message("警告: 网络异常: 无法连接 OKX API，已重试 5 次。")
    logging.error("所有重试失败")
    return None

# ============ 下单 ============
def place_order(side: str, price: float, size: float):
    flag = GLOBAL_FLAG
    try:
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        pos_side = "long" if side == "buy" else "short"
        sz = str(size)
        order = trade.place_order(instId=SYMBOL, tdMode="cross", side=side, posSide=pos_side, ordType="market", sz=sz)
        
        if order.get("code") == "0" and order.get("data") and order["data"][0].get("sCode") == "0":
            beijing_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
            
            print("\n" + "="*60)
            print(f"下单成功 {side.upper()} 成功".center(60))
            print(f"数量: {size} | 价格: {price:.2f} | 时间: {beijing_time}")
            print("="*60 + "\n")

            tg_msg = f"<b>下单成功 {side.upper()}</b>\n" \
                     f"数量: <code>{size}</code>\n" \
                     f"价格: <code>{price:.2f}</code>\n" \
                     f"时间: <code>{beijing_time}</code>"

            send_telegram_message(tg_msg)
            return True
        else:
            err = order.get("data", [{}])[0].get("sMsg", "") or order.get("msg", "未知")
            print(f"下单失败: {err}")
            send_telegram_message(f"下单失败: {err}")
    except Exception as e:
        send_telegram_message(f"下单异常: {e}")
        logging.error(f"下单异常: {traceback.format_exc()}")
    return False

# ============ 平仓 ============
def close_position():
    flag = GLOBAL_FLAG
    try:
        trade = Trade.TradeAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        acc = Account.AccountAPI(api_key=API_KEY, api_secret_key=SECRET_KEY, passphrase=PASS_PHRASE, flag=flag)
        for _ in range(3):
            positions = acc.get_positions(instId=SYMBOL)
            if positions.get("code") != "0":
                logging.warning(f"持仓查询失败: {positions.get('msg', '未知错误')}")
                time.sleep(2)
                continue

            pos_data = positions.get("data", [])
            if not pos_data:
                logging.info("当前无持仓")
                send_telegram_message("平仓完成: 当前无持仓")
                return True

            for pos in pos_data:
                pos_side = pos.get("posSide")
                if pos_side in ["long", "short"]:
                    r = trade.close_positions(instId=SYMBOL, mgnMode="cross", posSide=pos_side, autoCxl=False)
                    if r.get("code") == "0":
                        send_telegram_message(f"平仓成功: {pos_side.upper()} {pos.get('pos')} 张")
                        logging.info(f"平仓成功: {pos_side}")
                    else:
                        logging.warning(f"平仓失败: {r.get('msg')}")
            time.sleep(2)

        send_telegram_message("平仓超时或部分失败，请手动检查")
        return False
    except Exception as e:
        logging.error(f"平仓异常: {traceback.format_exc()}")
        send_telegram_message(f"平仓异常: {e}")
        return False

# ============ Pine → Python 转换器 ============
def convert_pine_to_python(pine_code: str) -> str:
    code = pine_code.strip()
    if not code:
        return ""

    params = {}
    clean_lines = []
    for line in code.split('\n'):
        line = line.strip()
        if not line or line.startswith('//'):
            clean_lines.append('')
            continue

        match = re.search(r'(\w+)\s*=\s*input\.[^(]*\(([^,)]+)', line)
        if match:
            name, default = match.groups()
            default = default.strip()
            try:
                val = float(default) if '.' in default else int(default)
            except:
                val = {"true": True, "false": False}.get(default.lower(), default.strip('"\''))
            params[name] = val
            clean_lines.append('')
            continue

        clean_lines.append(line)

    code = '\n'.join(clean_lines)
    code = re.sub(r'plot\([^)]*\)', '', code)
    code = re.sub(r'plotshape\([^)]*\)', '', code)
    code = re.sub(r'fill\([^)]*\)', '', code)
    code = re.sub(r'alertcondition\([^)]*\)', '', code)
    code = re.sub(r'input\([^)]*\)', '', code)

    code = re.sub(r'\bhl2\b', r'(df["high"] + df["low"]) / 2', code)
    code = re.sub(r'\bclose\[1\]\b', r'df["close"].iloc[-2] if len(df) > 1 else df["close"].iloc[-1]', code)
    code = re.sub(r'\bhigh\[1\]\b', r'df["high"].iloc[-2] if len(df) > 1 else df["high"].iloc[-1]', code)
    code = re.sub(r'\blow\[1\]\b', r'df["low"].iloc[-2] if len(df) > 1 else df["low"].iloc[-1]', code)

    code = re.sub(r'sma\(tr,\s*(\w+)\)', r'df["tr"].rolling(window=\1, min_periods=\1).mean().iloc[-1]', code)
    code = re.sub(r'atr\((\w+)\)', r'df["tr"].rolling(window=\1, min_periods=\1).mean().iloc[-1]', code)

    code = re.sub(r'(\w+)\s*:=', r'_state["\1"] =', code)
    code = re.sub(r'var\s+\w+\s+(\w+)\s*=\s*na', r'_state["\1"] = None', code)
    code = re.sub(r'var\s+\w+\s+(\w+)\s*=\s*([\d.]+)', r'_state["\1"] = \2', code)
    code = re.sub(r'nz\((\w+)\[1\],\s*\1\)', r'_state.get("\1", \1)', code)

    code = re.sub(r'([^?]+)\?\s*([^:]+)\s*:\s*(.+)', r'(\2) if (\1) else (\3)', code)

    python_code = f'''
import pandas as pd
PERIODS = {params.get("Periods", 10)}
MULTIPLIER = {params.get("Multiplier", 3.0)}
_state = {{"up": None, "dn": None, "trend": 1, "initialized": False}}
def generate_signal(data):
    global _state
    candles = data["candles"]
    if len(candles) < 15:
        return None
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"]).astype(float)
    df = df.iloc[::-1].reset_index(drop=True)
    df["tr0"] = abs(df["high"] - df["low"])
    df["tr1"] = abs(df["high"] - df["close"].shift(1))
    df["tr2"] = abs(df["low"] - df["close"].shift(1))
    df["tr"] = df[["tr0", "tr1", "tr2"]].max(axis=1)
    atr = df["tr"].rolling(window=PERIODS, min_periods=PERIODS).mean().iloc[-1]
    if pd.isna(atr):
        return None
    latest_idx = len(df) - 1
    src = (df["high"] + df["low"]) / 2
    up = src.iloc[latest_idx] - MULTIPLIER * atr
    dn = src.iloc[latest_idx] + MULTIPLIER * atr
    close_curr = df["close"].iloc[latest_idx]
    close_prev = df["close"].iloc[latest_idx - 1] if latest_idx > 0 else close_curr
    up_prev = _state["up"] if _state["initialized"] else up
    dn_prev = _state["dn"] if _state["initialized"] else dn
    if close_prev > up_prev:
        up = max(up, up_prev)
    if close_prev < dn_prev:
        dn = min(dn, dn_prev)
    trend = _state["trend"]
    if trend == -1 and close_curr > dn_prev:
        trend = 1
    elif trend == 1 and close_curr < up_prev:
        trend = -1
    prev_trend = _state["trend"]
    buy_signal = trend == 1 and prev_trend == -1
    sell_signal = trend == -1 and prev_trend == 1
    _state.update({{"up": up, "dn": dn, "trend": trend, "initialized": True}})
    if buy_signal: return "buy"
    if sell_signal: return "sell"
    return None
'''.strip()

    return python_code

def convert_strategy_code(raw_code: str) -> str:
    raw_code = raw_code.strip()
    if not raw_code:
        return ""

    pine_keywords = ["input(", "plot(", "strategy(", "study(", "=>", "hline(", "ta.", "var ", "alertcondition("]
    is_pine = any(kw in raw_code for kw in pine_keywords)

    if is_pine:
        logging.info("检测到 Pine Script，正在生成 Python 策略...")
        try:
            return convert_pine_to_python(raw_code)
        except Exception as e:
            raise ValueError(f"转换失败: {e}")

    match = re.search(r'def\s+generate_signal\s*\([^)]*\)\s*:\s*(.*)', raw_code, re.DOTALL | re.MULTILINE)
    if not match:
        raise ValueError("Python 策略必须包含 `def generate_signal(data):` 函数")

    user_body = match.group(1).strip()

    enhanced_template = f'''
import pandas as pd
from datetime import datetime, timezone, timedelta
BEIJING_TZ = timezone(timedelta(hours=8))
def generate_signal(data):
    candles = data["candles"]
    if len(candles) < 10:
        return None
    df = pd.DataFrame(candles, columns=["ts","open","high","low","close","volume","volCcy","volCcyQuote","confirm"]).astype(float)
    df = df.iloc[::-1].reset_index(drop=True)
    signal = None
    try:
        {user_body}
    except Exception as e:
        raise RuntimeError(f"用户策略代码错误: {{e}}") from e
    if signal in ["buy", "sell"]:
        return signal
    return None
'''.strip()

    return enhanced_template

# ============ 【核心修改】机器人主循环 - 集成风控引擎 ============
# ============ 【核心修改】混合监控主循环 ============
def run_bot():
    """
    混合监控机器人主循环
    
    架构:
    ┌─────────────────────────────────────┐
    │  主线程 (K线级别)                    │
    │  - 每根K线执行策略信号               │
    │  - 检查止盈、移动止损                │
    │  - 检查每日风控                      │
    └─────────────────────────────────────┘
              │
              ├──> 启动
              ↓
    ┌─────────────────────────────────────┐
    │  子线程 (实时监控)                   │
    │  - 每5秒检查持仓                     │
    │  - 仅处理紧急止损                    │
    │  - 闪崩保护、极端盈利                │
    └─────────────────────────────────────┘
    """
    global BOT_RUNNING, CONVERTED_STRATEGY_CODE, GLOBAL_FLAG
    
    mode = "模拟盘" if IS_DEMO else "实盘"
    logging.info("🚀 混合监控机器人启动")
    send_telegram_message(f"策略启动 (混合监控模式)\n{mode} | {SYMBOL} | {BAR_INTERVAL}")
    
    # 编译策略
    ns = {}
    try:
        exec(CONVERTED_STRATEGY_CODE, ns)
        generate_signal = ns.get("generate_signal")
        if not generate_signal:
            raise ValueError("未找到 generate_signal 函数")
        logging.info("✅ 策略编译成功")
    except Exception as e:
        logging.error(f"❌ 策略编译失败: {e}")
        send_telegram_message(f"策略编译失败: {e}")
        return
    
    # 初始化监控组件
    position_monitor = PositionMonitor(API_KEY, SECRET_KEY, PASS_PHRASE, GLOBAL_FLAG, SYMBOL)
    risk_manager = RiskManager()
    
    # 【关键】初始化实时监控线程
    realtime_monitor = RealtimeMonitor(API_KEY, SECRET_KEY, PASS_PHRASE, GLOBAL_FLAG, SYMBOL)
    
    # 设置紧急回调函数
    def handle_emergency_stop(profit_pct):
        """紧急止损回调"""
        logging.critical(f"🚨 实时监控触发紧急止损: {profit_pct:.2f}%")
        send_telegram_message(
            f"🚨 <b>紧急止损</b>\n"
            f"亏损: {profit_pct:.2f}%\n"
            f"触发时间: {datetime.now().strftime('%H:%M:%S')}\n"
            f"(5秒高频监控)"
        )
        close_position()
    
    def handle_flash_crash(profit_pct):
        """闪崩保护回调"""
        logging.critical(f"⚡ 闪崩检测: {profit_pct:.2f}%")
        send_telegram_message(
            f"⚡ <b>闪崩保护触发</b>\n"
            f"瞬间亏损: {profit_pct:.2f}%\n"
            f"已强制平仓"
        )
        close_position()
    
    def handle_extreme_profit(profit_pct):
        """极端盈利回调"""
        logging.info(f"🎉 极端盈利触发: {profit_pct:.2f}%")
        send_telegram_message(
            f"🎉 <b>极端盈利止盈</b>\n"
            f"盈利: {profit_pct:.2f}%\n"
            f"立即锁定利润"
        )
        close_position()
    
    realtime_monitor.on_emergency_stop = handle_emergency_stop
    realtime_monitor.on_flash_crash = handle_flash_crash
    realtime_monitor.on_extreme_profit = handle_extreme_profit
    
    # 启动实时监控线程
    realtime_monitor.start()
    
    last_signal = None
    last_processed_ts = None
    
    try:
        while BOT_RUNNING:
            try:
                # ========== K线级别检查 (主线程) ==========
                
                # 1. 获取最新K线
                data = get_latest_price_and_indicators(SYMBOL, BAR_INTERVAL, max_retries=5)
                if not data:
                    time.sleep(10)
                    continue
                
                current_bar_ts = int(data["candles"][-1][0])
                if current_bar_ts == last_processed_ts:
                    time.sleep(3)
                    continue
                
                # 2. 新K线触发
                last_processed_ts = current_bar_ts
                kline_time = datetime.fromtimestamp(current_bar_ts / 1000, tz=timezone(timedelta(hours=8)))
                logging.info(f"{'='*25} 新K线 {BAR_INTERVAL} | {kline_time.strftime('%H:%M:%S')} {'='*25}")
                
                # 3. 更新持仓监控
                position_info = position_monitor.update()
                
                if position_info:
                    print(generate_position_report(position_info))
                    
                    # 【K线检查1】移动止损 (只在K线级别检查)
                    if HybridMonitorConfig.KLINE_CHECKS["trailing_stop"]:
                        should_stop, reason = risk_manager.check_stop_loss(position_info)
                        if should_stop and "移动止损" in reason:
                            logging.warning(f"📉 K线级别触发: {reason}")
                            send_telegram_message(f"📉 <b>移动止损</b>\n{reason}\n(K线收盘检查)")
                            close_position()
                            time.sleep(3)
                            continue
                    
                    # 【K线检查2】止盈 (多级止盈)
                    if HybridMonitorConfig.KLINE_CHECKS["take_profit"]:
                        should_profit, ratio, reason = risk_manager.check_take_profit(position_info)
                        if should_profit:
                            logging.info(f"📈 K线级别触发: {reason}")
                            send_telegram_message(f"📈 <b>止盈</b>\n{reason}\n(K线收盘检查)")
                            
                            if ratio >= 1.0:
                                close_position()
                            else:
                                partial_close_position(ratio)
                            
                            time.sleep(3)
                            continue
                    
                    # 【K线检查3】时间止损
                    if HybridMonitorConfig.KLINE_CHECKS["time_stop"]:
                        if position_info["bars_held"] >= RiskConfig.MAX_HOLD_BARS:
                            logging.warning(f"⏰ 时间止损: 持仓{position_info['bars_held']}根K线")
                            send_telegram_message(f"⏰ <b>时间止损</b>\n持仓过久: {position_info['bars_held']}根K线")
                            close_position()
                            time.sleep(3)
                            continue
                
                # 4. 检查每日风控
                current_balance = get_account_balance()
                if current_balance:
                    if risk_manager.new_day_reset(current_balance):
                        logging.info("🌅 新的一天开始")
                        send_telegram_message(
                            f"🌅 <b>新的一天</b>\n"
                            f"初始资金: {current_balance:.2f} USDT\n"
                            f"盈利目标: {RiskConfig.DAILY_PROFIT_TARGET}%"
                        )
                    
                    limit_hit, msg = risk_manager.check_daily_limits(current_balance)
                    if limit_hit:
                        logging.warning(f"🛑 每日限制: {msg}")
                        send_telegram_message(f"🛑 <b>每日限制</b>\n{msg}")
                        close_position()
                        time.sleep(3600)
                        continue
                
                if risk_manager.stopped_trading:
                    time.sleep({"1m":55,"3m":150,"5m":250,"15m":850}.get(BAR_INTERVAL, 30))
                    continue
                
                # 5. 执行策略信号
                if HybridMonitorConfig.KLINE_CHECKS["strategy_signal"]:
                    signal = generate_signal(data)
                    if signal and signal != last_signal:
                        close_position()
                        time.sleep(3)
                        
                        if place_order(signal, data["price"], ORDER_SIZE):
                            send_telegram_message(f"📊 开{signal.upper()}仓\n价格: {data['price']:.2f}")
                            last_signal = signal
                
                # 6. 等待下一根K线
                wait_seconds = {"1m":55,"3m":150,"5m":250,"15m":850,"1H":3500}.get(BAR_INTERVAL, 30)
                logging.info(f"⏳ 等待下一根K线 ({wait_seconds}s) | 实时监控运行中...")
                time.sleep(wait_seconds)
                
            except Exception as e:
                logging.error(f"主循环异常: {e}")
                time.sleep(10)
    
    finally:
        # 停止实时监控线程
        realtime_monitor.stop()
        logging.info("机器人已停止")

# ============ HTML 模板 ============
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>OKX 策略启动器(实盘/模拟盘)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body{font-family:Arial;margin:40px;background:#f4f4f4}
        .c{max-width:1000px;margin:auto;background:#fff;padding:30px;border-radius:10px;box-shadow:0 0 10px rgba(0,0,0,.1)}
        input,select,textarea{width:100%;padding:12px;margin:8px 0;border:1px solid #ccc;border-radius:6px;font-size:15px}
        button{background:#28a745;color:#fff;padding:15px;border:none;border-radius:6px;cursor:pointer;font-size:18px;font-weight:bold;width:100%;margin:10px 0}
        .cancel-btn{background:#dc3545}
        button:hover{background:#218838}
        .cancel-btn:hover{background:#c82333}
        .s{color:#28a745;font-weight:bold}
        .e{color:#dc3545;font-weight:bold}
        .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:15px;margin:20px 0}
        .box{background:#fff;padding:15px;border-radius:6px;text-align:center;border:1px solid #ddd}
        .config-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}
        .tip{font-size:13px;color:#28a745;margin-top:5px}
    </style>
</head>
<body>
<div class="c">
    <h1>OKX 策略启动器 - 增强版风控</h1>
    <p style="background:#e7f3ff;padding:10px;border-radius:5px;border-left:4px solid #007bff">
        <strong>🛡️ 新增风控功能:</strong><br>
        ✅ 固定止损: -2% | ✅ 移动止损: 峰值回撤1%<br>
        ✅ 多级止盈: 1.5%/3%/5% | ✅ 每日限制: 盈利3%或亏损5%停止<br>
        ✅ 连续亏损保护 | ✅ 实时持仓监控
    </p>
    {% if error %}
        <p class="e">{{ error }}</p>
    {% endif %}
    {% if success %}
        <p class="s">策略运行中!</p>
        <div class="grid">
            <div class="box"><h3>交易对</h3><p>{{ symbol }}</p></div>
            <div class="box"><h3>K线周期</h3><p>{{ bar }}</p></div>
            <div class="box"><h3>金额</h3><p>{{ order_size }}</p></div>
            <div class="box"><h3>交易模式</h3><p>{{ mode }}</p></div>
        </div>
        <form method="post" action="/cancel">
            <button type="submit" class="cancel-btn">取消策略</button>
        </form>
    {% else %}
    <form method="post">
        <div class="config-grid">
            <div><label><strong>OKX API Key</strong></label><input name="api_key" placeholder="输入 API Key" value="{{ api_key or '' }}"></div>
            <div><label><strong>OKX Secret Key</strong></label><input name="secret_key" placeholder="输入 Secret Key" value="{{ secret_key or '' }}"></div>
            <div><label><strong>OKX Passphrase</strong></label><input name="pass_phrase" placeholder="输入 Passphrase" value="{{ pass_phrase or '' }}"></div>
            <div><label><strong>Telegram Bot Token</strong></label><input name="bot_token" placeholder="输入 Bot Token" value="{{ bot_token or '' }}"></div>
            <div><label><strong>Telegram Chat ID</strong></label><input name="chat_id" placeholder="输入 Chat ID" value="{{ chat_id or '' }}"></div>
        </div>
        <label><strong>交易对</strong></label>
        <input name="symbol" placeholder="BTC-USDT-SWAP" value="{{ symbol or '' }}">
        <label><strong>K线周期</strong></label>
        <select name="bar">
            <option value="1m" {% if bar == "1m" %}selected{% endif %}>1 分钟</option>
            <option value="3m" {% if bar == "3m" %}selected{% endif %}>3 分钟</option>
            <option value="5m" {% if bar == "5m" %}selected{% endif %}>5 分钟</option>
            <option value="15m" {% if bar == "15m" %}selected{% endif %}>15 分钟</option>
            <option value="1H" {% if bar == "1H" %}selected{% endif %}>1 小时</option>
        </select>
        <label><strong>下单金额</strong></label>
        <input name="order_size" type="number" step="0.001" placeholder="0.01" value="{{ order_size or '0.01' }}">
        <label><strong>交易模式</strong></label>
        <select name="trade_mode">
            <option value="real" {% if not demo %}selected{% endif %}>实盘交易</option>
            <option value="demo" {% if demo %}selected{% endif %}>模拟盘(模拟)</option>
        </select>
        <label><strong>策略代码 (粘贴 Pine Script 或 Python)</strong></label>
        <textarea name="strategy_code" rows="15" placeholder="//@version=5\nindicator(...)">{{ default_code }}</textarea>
        <button type="submit">启动策略</button>
    </form>
    {% endif %}
</div>
</body>
</html>
'''

DEFAULT_PINE_EXAMPLE = '''
//@version=5
indicator("SuperTrend", overlay=true)
Periods = input(10)
Multiplier = input(3.0)
atr = ta.atr(Periods)
up = hl2 - Multiplier * atr
dn = hl2 + Multiplier * atr
var float trend = 1
var float up_prev = na
var float dn_prev = na
up := close[1] > up_prev ? max(up, up_prev) : up
dn := close[1] < dn_prev ? min(dn, dn_prev) : dn
trend := close > dn_prev ? 1 : close < up_prev ? -1 : trend
up_prev := up
dn_prev := dn
'''

# ============ Flask 路由 ============
@app.route('/', methods=['GET', 'POST'])
def index():
    global SYMBOL, BAR_INTERVAL, ORDER_SIZE, CONVERTED_STRATEGY_CODE, BOT_RUNNING, BOT_THREAD
    global API_KEY, SECRET_KEY, PASS_PHRASE, BOT_TOKEN, CHAT_ID, USER_STRATEGY_CODE, IS_DEMO, GLOBAL_FLAG

    configs = [(i, c) for i, c in enumerate(load_config_history())]
    is_running = load_bot_state()

    if request.method == 'POST':
        symbol = request.form.get('symbol', '').strip() or DEFAULT_SYMBOL
        bar = request.form.get('bar', '1m').strip()
        order_size_str = request.form.get('order_size', '0.01').strip()
        trade_mode = request.form.get('trade_mode', 'real').strip()
        IS_DEMO = (trade_mode == 'demo')
        GLOBAL_FLAG = "1" if IS_DEMO else "0"
        strategy_code = request.form.get('strategy_code', '').strip()
        api_key = request.form.get('api_key', '').strip()
        secret_key = request.form.get('secret_key', '').strip()
        pass_phrase = request.form.get('pass_phrase', '').strip()
        bot_token = request.form.get('bot_token', '').strip()
        chat_id = request.form.get('chat_id', '').strip()

        if not all([api_key, secret_key, pass_phrase, bot_token, chat_id]):
            return render_template_string(HTML_TEMPLATE, error="请填写所有配置!", default_code=strategy_code or DEFAULT_PINE_EXAMPLE)

        if not strategy_code.strip():
            return render_template_string(HTML_TEMPLATE, error="策略代码不能为空!", default_code=strategy_code)

        try:
            order_size = float(order_size_str)
            if order_size <= 0: raise ValueError
        except:
            return render_template_string(HTML_TEMPLATE, error="下单金额必须是正数!", default_code=strategy_code)

        try:
            CONVERTED_STRATEGY_CODE = convert_strategy_code(strategy_code)
        except Exception as e:
            return render_template_string(HTML_TEMPLATE, error=f"策略转换错误: {str(e)}", default_code=strategy_code)

        save_config_history(api_key, secret_key, pass_phrase, bot_token, chat_id)

        SYMBOL = symbol
        BAR_INTERVAL = bar
        ORDER_SIZE = order_size
        API_KEY = api_key
        SECRET_KEY = secret_key
        PASS_PHRASE = pass_phrase
        BOT_TOKEN = bot_token
        CHAT_ID = chat_id
        USER_STRATEGY_CODE = strategy_code

        BOT_RUNNING = True
        save_bot_state()
        BOT_THREAD = Thread(target=run_bot, daemon=True)
        BOT_THREAD.start()

        mode = "模拟盘" if IS_DEMO else "实盘"
        return render_template_string(HTML_TEMPLATE, success=True, symbol=SYMBOL, bar=BAR_INTERVAL, order_size=ORDER_SIZE, mode=mode)

    if is_running and BOT_RUNNING:
        mode = "模拟盘" if IS_DEMO else "实盘"
        return render_template_string(HTML_TEMPLATE, success=True, symbol=SYMBOL, bar=BAR_INTERVAL, order_size=ORDER_SIZE, mode=mode)

    return render_template_string(HTML_TEMPLATE, default_code=USER_STRATEGY_CODE or DEFAULT_PINE_EXAMPLE, symbol=SYMBOL, bar=BAR_INTERVAL, order_size=str(ORDER_SIZE), demo=IS_DEMO)

@app.route('/cancel', methods=['POST'])
def cancel():
    global BOT_RUNNING, BOT_THREAD

    BOT_RUNNING = False
    close_position()
    send_telegram_message("策略已取消")

    if BOT_THREAD:
        BOT_THREAD.join(timeout=5)
    BOT_THREAD = None

    if os.path.exists(STATE_FILE):
        try:
            os.remove(STATE_FILE)
        except:
            pass

    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta http-equiv="refresh" content="1;url=/">
        <style>
            body{font-family:Arial;background:#f4f4f4;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
            .msg{background:#fff;padding:30px;border-radius:10px;box-shadow:0 0 10px rgba(0,0,0,.1);text-align:center}
        </style>
    </head>
    <body>
        <div class="msg"><h2>策略已取消</h2><p>页面即将刷新...</p></div>
    </body>
    </html>
    '''

@app.route('/health')
def health():
    return "OK", 200

if load_bot_state() and BOT_RUNNING and CONVERTED_STRATEGY_CODE:
    BOT_THREAD = Thread(target=run_bot, daemon=True)
    BOT_THREAD.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
