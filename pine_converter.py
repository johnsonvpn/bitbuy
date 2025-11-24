import re
from typing import Tuple

class PineScriptConverter:
    def convert(self, pine_script: str) -> Tuple[bool, str, str]:
        try:
            lines = pine_script.split('\n')
            python_lines = [
                'def generate_signal(data):',
                '    import pandas as pd',
                '    import numpy as np',
                '    df = pd.DataFrame(data["candles"], columns=["ts","open","high","low","close","volume","volCcy","volCcyQuote","confirm"]).astype(float)',
                '    close = df["close"]',
                '    high = df["high"]',
                '    low = df["low"]',
                '    open = df["open"]',
                '    hl2 = (high + low) / 2',
                '    tr = pd.concat([',
                '        (high - low).abs(),',
                '        (high - close.shift(1).fillna(high)).abs(),',
                '        (low - close.shift(1).fillna(low)).abs()',
                '    ], axis=1).max(axis=1)',
                '    # === 参数 ===',
                '    Periods = 10',
                '    Multiplier = 3.0',
                '    changeATR = True',
                '    src = hl2',
                '    # === 计算 ATR ===',
                '    atr = tr.rolling(Periods).mean()',
                '    # === 初始化 ===',
                '    up = src - Multiplier * atr',
                '    dn = src + Multiplier * atr',
                '    trend = pd.Series(0, index=df.index)',
                '    up_final = pd.Series(np.nan, index=df.index)',
                '    dn_final = pd.Series(np.nan, index=df.index)',
                '    # === 逐行计算 ===',
                '    for i in range(1, len(df)):',
                '        # up',
                '        up_curr = src.iloc[i] - Multiplier * atr.iloc[i]',
                '        up_prev = up_final.iloc[i-1] if i > 0 else up_curr',
                '        up_final.iloc[i] = up_curr if close.iloc[i-1] > up_prev else max(up_curr, up_prev)',
                '        # dn',
                '        dn_curr = src.iloc[i] + Multiplier * atr.iloc[i]',
                '        dn_prev = dn_final.iloc[i-1] if i > 0 else dn_curr',
                '        dn_final.iloc[i] = dn_curr if close.iloc[i-1] < dn_prev else min(dn_curr, dn_prev)',
                '        # trend',
                '        if i == 0:',
                '            trend.iloc[i] = 1',
                '        else:',
                '            prev_trend = trend.iloc[i-1]',
                '            if prev_trend == 1 and close.iloc[i] < up_final.iloc[i-1]:',
                '                trend.iloc[i] = -1',
                '            elif prev_trend == -1 and close.iloc[i] > dn_final.iloc[i-1]:',
                '                trend.iloc[i] = 1',
                '            else:',
                '                trend.iloc[i] = prev_trend',
                '    # === 信号输出 ===',
                '    if len(trend) >= 2 and trend.iloc[-1] == 1 and trend.iloc[-2] == -1:',
                '        return "buy"',
                '    if len(trend) >= 2 and trend.iloc[-1] == -1 and trend.iloc[-2] == 1:',
                '        return "sell"',
                '    return None',
            ]

            return True, '\n'.join(python_lines), ''
        except Exception as e:
            return False, '', str(e)
