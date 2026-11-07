# -*- coding: utf-8 -*-
# 由 `python research/cli.py export` 生成，请勿手工编辑。
# 盘前由人工导入券商标准版 QMT 内置 Python 沙箱；禁止实时桥接。
# 规则指纹：32d1ca1e87adc307f47e2bf5e2c1a52edd9ed62bc6809fd8a42c9d3aca6089ed
# 策略内核哈希：1c0e083c61dd46c1b95f4211c415f20ee7155a5ee14cbc39c5488dce62a3bab6
RULE_DIGEST = '32d1ca1e87adc307f47e2bf5e2c1a52edd9ed62bc6809fd8a42c9d3aca6089ed'
CORE_SHA256 = '1c0e083c61dd46c1b95f4211c415f20ee7155a5ee14cbc39c5488dce62a3bab6'
RULES = {'buy': {'conditions': [{'field': 'pct_chg', 'op': 'between', 'value': [0.5, 6.0]},
                        {'field': 'volume_ratio', 'op': '>=', 'value': 1.0}],
         'logic': 'AND',
         'precision_conditions': [{'field': 'ma_bull', 'op': '==', 'value': True},
                                  {'field': 'macd_hist', 'op': '>', 'value': 0},
                                  {'field': 'rsi6', 'op': '<', 'value': 75}],
         'precision_logic': 'AND'},
 'cooldown_minutes': 30,
 'mode': 'semi_auto',
 'name': '浙商证券 QMT · 盘中实时选股与自动买卖',
 'note': '这是唯一生效的生产规则文件。所有阈值均为占位示例值，必须由使用者自行确认后修改，不是投资建议。买入在全市场扫描，卖出只针对已持有且可卖的仓位。',
 'risk': {'lot_size': 100,
          'max_daily_loss_pct': 2,
          'max_daily_orders': 20,
          'max_daily_turnover': 200000,
          'max_order_amount': 20000,
          'max_position_pct': 10,
          'max_total_position_pct': 80},
 'sell': {'conditions': [{'field': 'pnl_pct', 'op': '<=', 'value': -3.0},
                         {'field': 'drawdown_from_high_pct', 'op': '<=', 'value': -4.0},
                         {'field': 'pct_chg_5m', 'op': '<=', 'value': -2.0},
                         {'field': 'hold_minutes', 'op': '>=', 'value': 240}],
          'logic': 'OR',
          'precision_conditions': [],
          'precision_logic': 'AND'},
 'time_windows': [['09:35', '11:25'], ['13:05', '14:50']],
 'universe': {'boards': ['主板', '创业板'],
              'exclude_limit_down': False,
              'exclude_limit_up': True,
              'exclude_st': True,
              'exclude_suspended': True,
              'min_amount': 10000000,
              'min_list_days': 60},
 'version': '2.0.0'}

