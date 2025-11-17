# -*- coding: utf-8 -*-
"""
EMA密集突破策略 - 5分钟K线回测（优化版）
作者：Grok 优化
日期：2025-11-05
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pandas_ta import ema
import warnings
warnings.filterwarnings("ignore")

# ==================== 配置区（可通过注释修改） ====================
# MAX_TRADES_PER_DAY = 3
# CONFIRM_BARS = 2
# DENSE_MIN_BARS = 6
# DENSE_THRESHOLD = 0.0025  # 0.25%
# VOLUME_MULTIPLIER = 1.2
# RISK_PER_TRADE = 0.01     # 1%

# ==================== 1. 数据加载 ====================
def load_data(csv_path):
    df = pd.read_csv(csv_path, parse_dates=['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)
    return df

# ==================== 2. 策略核心类 ====================
class EMA_DenseBreakout_Strategy:
    def __init__(self, initial_capital=100000, risk_per_trade=0.01):
        self.capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.position = 0
        self.entry_price = 0
        self.direction = 0
        self.trades = []
        self.equity_curve = []
        self.daily_trades = {}  # {date: count}

        # === 可配置参数 ===
        self.max_trades_per_day = self._get_config("MAX_TRADES_PER_DAY", 3)
        self.confirm_bars = self._get_config("CONFIRM_BARS", 2)
        self.dense_min_bars = self._get_config("DENSE_MIN_BARS", 6)
        self.dense_threshold = self._get_config("DENSE_THRESHOLD", 0.0025)
        self.volume_multiplier = self._get_config("VOLUME_MULTIPLIER", 1.2)

    def _get_config(self, name, default):
        """从脚本顶部注释读取配置"""
        import inspect, re
        source = inspect.getsource(self.__class__)
        match = re.search(fr"#\s*{name}\s*=\s*([0-9.]+)", source)
        return float(match.group(1)) if match else default

    def calculate_indicators(self, df):
        df['ema20'] = ema(df['close'], length=20)
        df['ema60'] = ema(df['close'], length=60)
        df['ema120'] = ema(df['close'], length=120)
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        return df

    def is_dense(self, row):
        emas = [row['ema20'], row['ema60'], row['ema120']]
        if any(pd.isna(x) for x in emas):
            return False
        return (max(emas) - min(emas)) / row['close'] <= self.dense_threshold

    def generate_signals(self, df):
        df['dense'] = df.apply(self.is_dense, axis=1)
        df['dense_start'] = (df['dense'] & ~df['dense'].shift(1).fillna(False))
        df['dense_bars'] = df.groupby(df['dense_start'].cumsum())['dense'].cumsum()
        df['dense_zone_high'] = df[['ema20','ema60','ema120']].max(axis=1).where(df['dense'])
        df['dense_zone_low'] = df[['ema20','ema60','ema120']].min(axis=1).where(df['dense'])

        df['long_setup'] = False
        df['short_setup'] = False
        df['long_signal'] = False
        df['short_signal'] = False

        in_dense = False
        dense_high = dense_low = 0
        breakout_high = breakout_low = 0

        for i in range(1, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i-1]

            # === 进入密集区 ===
            if row['dense'] and row['dense_bars'] >= self.dense_min_bars:
                in_dense = True
                dense_high = max(dense_high, row['dense_zone_high'])
                dense_low = min(dense_low, row['dense_zone_low']) if dense_low else row['dense_zone_low']
            else:
                if in_dense and prev['dense']:
                    # 离开密集区 → 记录 setup
                    if row['close'] > dense_high and row['volume'] > row['vol_ma20'] * self.volume_multiplier:
                        df.loc[df.index[i], 'long_setup'] = True
                        breakout_high = dense_high
                    elif row['close'] < dense_low and row['volume'] > row['vol_ma20'] * self.volume_multiplier:
                        df.loc[df.index[i], 'short_setup'] = True
                        breakout_low = dense_low
                in_dense = False
                dense_high = dense_low = 0

            # === 信号确认：K线收盘后 1 根确认 + 连续 confirm_bars 根收在突破方向 ===
            if df.loc[df.index[i], 'long_setup']:
                confirm_count = 0
                for j in range(i, min(i + self.confirm_bars, len(df))):
                    if df.loc[df.index[j], 'close'] > breakout_high:
                        confirm_count += 1
                    else:
                        break
                if confirm_count >= self.confirm_bars:
                    signal_idx = i + self.confirm_bars - 1
                    if signal_idx < len(df):
                        df.loc[df.index[signal_idx], 'long_signal'] = True

            if df.loc[df.index[i], 'short_setup']:
                confirm_count = 0
                for j in range(i, min(i + self.confirm_bars, len(df))):
                    if df.loc[df.index[j], 'close'] < breakout_low:
                        confirm_count += 1
                    else:
                        break
                if confirm_count >= self.confirm_bars:
                    signal_idx = i + self.confirm_bars - 1
                    if signal_idx < len(df):
                        df.loc[df.index[signal_idx], 'short_signal'] = True

        return df

    def run(self, df):
        df = self.calculate_indicators(df)
        df = self.generate_signals(df)

        capital = self.capital
        position = 0
        entry_price = 0
        direction = 0
        entry_time = None
        take_profit1 = take_profit2 = 0

        for i in range(len(df)):
            row = df.iloc[i]
            close = row['close']
            date = row['datetime'].date()

            # === 每日交易次数限制 ===
            if date not in self.daily_trades:
                self.daily_trades[date] = 0

            # === 退出逻辑 ===
            if position != 0:
                # 移动止损：EMA20
                if direction == 1 and close < row['ema20']:
                    profit = (close - entry_price) * position
                    capital += profit
                    self._record_trade(entry_time, row['datetime'], 'long', entry_price, close, profit)
                    position = entry_price = direction = 0
                    take_profit1 = take_profit2 = 0

                elif direction == -1 and close > row['ema20']:
                    profit = (entry_price - close) * abs(position)
                    capital += profit
                    self._record_trade(entry_time, row['datetime'], 'short', entry_price, close, profit)
                    position = entry_price = direction = 0
                    take_profit1 = take_profit2 = 0

            # === 入场逻辑 ===
            if position == 0 and self.daily_trades[date] < self.max_trades_per_day:
                risk_amount = capital * self.risk_per_trade

                if row['long_signal']:
                    stop_loss = row['dense_zone_low'] * 0.999 if pd.notna(row['dense_zone_low']) else close * 0.98
                    risk_per_share = close - stop_loss
                    if risk_per_share > 0:
                        position = risk_amount / risk_per_share
                        position = np.floor(position / 100) * 100
                        if position > 0:
                            entry_price = close
                            direction = 1
                            entry_time = row['datetime']
                            take_profit1 = entry_price + 1.5 * risk_per_share
                            take_profit2 = entry_price + 3.0 * risk_per_share
                            self.daily_trades[date] += 1

                elif row['short_signal']:
                    stop_loss = row['dense_zone_high'] * 1.001 if pd.notna(row['dense_zone_high']) else close * 1.02
                    risk_per_share = stop_loss - close
                    if risk_per_share > 0:
                        position = - (risk_amount / risk_per_share)
                        position = -np.floor(abs(position) / 100) * 100
                        if position < 0:
                            entry_price = close
                            direction = -1
                            entry_time = row['datetime']
                            take_profit1 = entry_price - 1.5 * risk_per_share
                            take_profit2 = entry_price - 3.0 * risk_per_share
                            self.daily_trades[date] += 1

            self.equity_curve.append(capital)

        self.capital = capital
        self.position = position
        return df

    def _record_trade(self, entry_time, exit_time, direction, entry, exit, profit):
        self.trades.append({
            'entry_time': entry_time,
            'exit_time': exit_time,
            'direction': direction,
            'entry': entry,
            'exit': exit,
            'profit': profit,
            'return_pct': (exit/entry - 1) if direction == 'long' else (entry/exit - 1)
        })

    def performance(self):
        trades = pd.DataFrame(self.trades)
        if len(trades) == 0:
            print("无交易记录")
            return

        win_rate = len(trades[trades['profit'] > 0]) / len(trades)
        total_return = (self.capital / 100000 - 1) * 100
        equity = pd.Series(self.equity_curve)
        drawdown = (equity / equity.cummax() - 1).min() * 100

        print("="*60)
        print("              回测结果（优化版）")
        print("="*60)
        print(f"总交易次数: {len(trades)}")
        print(f"每日限额: {self.max_trades_per_day} 次")
        print(f"胜率: {win_rate:.1%}")
        print(f"总收益率: {total_return:+.2f}%")
        print(f"最大回撤: {drawdown:.2f}%")
        print(f"最终资金: {self.capital:,.2f}")
        if len(trades) > 0:
            avg_win = trades[trades['profit']>0]['return_pct'].mean()
            avg_loss = abs(trades[trades['profit']<0]['return_pct'].mean())
            print(f"盈亏比: {avg_win / avg_loss:.2f}" if avg_loss > 0 else "盈亏比: N/A")

        # 绘图
        plt.figure(figsize=(14, 6))
        plt.plot(equity.index, equity, label='净值曲线', color='blue')
        plt.title('EMA密集突破策略 - 净值曲线')
        plt.xlabel('K线根数')
        plt.ylabel('资金')
        plt.legend()
        plt.grid(True)
        plt.tight_layout()
        plt.show()

# ==================== 4. 主程序 ====================
if __name__ == "__main__":
    # 修改为你的CSV路径
    df = load_data("your_5min_data.csv")

    strategy = EMA_DenseBreakout_Strategy(initial_capital=100000, risk_per_trade=0.01)
    df = strategy.run(df)
    strategy.performance()

    # 保存信号
    df[['datetime','close','ema20','ema60','ema120','dense','long_signal','short_signal']].to_csv("signals_optimized.csv", index=False)
    print("\n信号已保存至 signals_optimized.csv")
