import sys
sys.path.insert(0, '.')
from okx_trading_bot_v2 import fetch_klines, detect_peaks_valleys, is_above_all_ma, is_below_all_ma, get_last_peak, get_last_valley, SYMBOL
import pandas as pd

df1h = fetch_klines(SYMBOL, '1h', limit=24)
if df1h is None:
    print('获取K线失败')
    exit()

peaks, valleys = detect_peaks_valleys(df1h)

print('='*80)
print(f'今日1h K线分析报告 ({SYMBOL})')
print('='*80)
print()

print('时间'.ljust(20) + '开盘'.rjust(10) + '最高'.rjust(10) + '最低'.rjust(10) + '收盘'.rjust(10) + '类型'.ljust(10))
print('-'*70)
for i in range(len(df1h)):
    row = df1h.iloc[i]
    time_str = str(row.name)  # timestamp是索引
    is_peak = any(p[0] == i for p in peaks)
    is_valley = any(v[0] == i for v in valleys)
    pattern = ''
    if is_peak:
        pattern = '波峰'
    elif is_valley:
        pattern = '波谷'
    print(f'{time_str.ljust(20)} {row["open"]:>10.2f} {row["high"]:>10.2f} {row["low"]:>10.2f} {row["close"]:>10.2f} {pattern.ljust(10)}')

print()
print('='*80)
print('波峰列表:')
print('-'*30)
for idx, price in peaks:
    time_str = str(df1h.index[idx])
    print(f'{time_str} -> ${price:.2f}')

print()
print('波谷列表:')
print('-'*30)
for idx, price in valleys:
    time_str = str(df1h.index[idx])
    print(f'{time_str} -> ${price:.2f}')

print()
print('='*80)
print('模拟策略下单:')
print('-'*30)

position = None
entry_price = None
tracking_peak = None
tracking_valley = None

for i in range(len(df1h)):
    row = df1h.iloc[i]
    time_str = str(row.name)  # timestamp是索引
    current_price = row['close']
    
    if not position:
        df_slice = df1h.iloc[:i+1]
        long_ok, _ = is_above_all_ma(df_slice)
        short_ok, _ = is_below_all_ma(df_slice)
        
        if long_ok:
            position = 'long'
            entry_price = current_price
            tracking_valley = get_last_valley(df_slice)
            print(f'✅ [{time_str}] 开多仓 @ ${current_price:.2f} (跌破波谷 ${tracking_valley:.2f} 止损)')
        elif short_ok:
            position = 'short'
            entry_price = current_price
            tracking_peak = get_last_peak(df_slice)
            print(f'⬇️ [{time_str}] 开空仓 @ ${current_price:.2f} (突破波峰 ${tracking_peak:.2f} 止损)')
    
    else:
        if position == 'long':
            current_valley = get_last_valley(df1h.iloc[:i+1])
            if tracking_valley and current_price < tracking_valley:
                profit = ((current_price - entry_price) / entry_price) * 100
                print(f'❌ [{time_str}] 多单平仓 @ ${current_price:.2f} (跌破波谷) | 盈利: {profit:.2f}%')
                position = None
                tracking_valley = None
            elif current_valley and current_valley < tracking_valley:
                tracking_valley = current_valley
                print(f'📉 [{time_str}] 更新跟踪波谷: ${tracking_valley:.2f}')
        else:
            current_peak = get_last_peak(df1h.iloc[:i+1])
            if tracking_peak and current_price > tracking_peak:
                profit = ((entry_price - current_price) / entry_price) * 100
                print(f'❌ [{time_str}] 空单平仓 @ ${current_price:.2f} (突破波峰) | 盈利: {profit:.2f}%')
                position = None
                tracking_peak = None
            elif current_peak and current_peak > tracking_peak:
                tracking_peak = current_peak
                print(f'📈 [{time_str}] 更新跟踪波峰: ${tracking_peak:.2f}')

print()
print('='*80)
if position:
    print(f'当前持仓: {position} | 开仓价: ${entry_price:.2f}')
else:
    print('当前持仓: 无')
