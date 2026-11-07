# 指标字典与规则 DSL

> 所有阈值均为**可配置示例值**，不是投资建议。默认值参考金融技能方法论（`monitor-alert` 的 14 类预警规则），需由你在 `http://127.0.0.1:8765/config.html` 确认后写入 `shared/rules/live.json`。

## 一、两级筛选（性能约束）

盘中全市场约 5000+ 标的，**K 线类指标需逐只串行取历史 K 线**，不能全市场算。因此：

- **一级（快照类，全市场粗筛）**：只依赖最新快照 + 少量历史 bar，可批量计算。
- **二级（K 线类，候选池精算）**：建议候选池 ≤20 只，逐只计算 MA / MACD / RSI / KDJ / BOLL / 突破形态。

## 二、快照类指标（一级）

| 字段 | 含义 | 单位 / 口径 | 示例阈值 |
|---|---|---|---|
| `last` | 最新价 | 元 | — |
| `pct_chg` | 相对昨收涨跌幅 | % | ±4%（A 股异动） |
| `pct_chg_1m` / `pct_chg_3m` / `pct_chg_5m` | 1/3/5 分钟涨速 | % | 涨速 >1% |
| `turnover_rate` | 换手率 | % | >3% |
| `volume_ratio` | 量比 | 倍 | >1.5 |
| `amount` | 成交额 | 元 | >1e8 |
| `amplitude` | 振幅 | % | <8% |
| `vol_x` | 成交量 / 过去 N 日均量 | 倍 | ≥2.0（放量） |
| `bid_ask_ratio` | 委比 | % | >0 |
| `net_inflow` | 主力净流入 | 元 | >5e7 |
| `high_limit` / `low_limit` | 是否涨停 / 跌停 | bool | 买入时排除 |
| `suspended` | 是否停牌 | bool | 排除 |
| `is_st` | 是否 ST / *ST | bool | 排除 |
| `board` | 板块（主板/创业板/科创板/北交所） | str | 按权限白名单 |
| `list_days` | 上市天数 | 天 | >60 |

## 三、K 线类指标（二级）

| 字段 | 算法 | 默认参数 |
|---|---|---|
| `ma5` / `ma10` / `ma20` / `ma60` | 简单移动平均 | 5/10/20/60 |
| `ema12` / `ema26` | 指数移动平均 | 12/26 |
| `macd_dif` / `macd_dea` / `macd_hist` | DIF=EMA12−EMA26；DEA=DIF 的 9 日 EMA；HIST=2×(DIF−DEA) | 12/26/9 |
| `rsi6` / `rsi12` | 相对强弱（SMA 平滑） | 6/12；超买 70 / 超卖 30 |
| `kdj_k` / `kdj_d` / `kdj_j` | 9 日 RSV，K/D 用 3 日 SMA | 9/3/3 |
| `boll_upper` / `boll_lower` / `boll_mid` | MA20 ± 2×标准差 | 20/2 |
| `breakout_20d` | 是否突破过去 20 日高点 | 20 |
| `ma_bull` | MA5 > MA10 > MA20 | bool |

## 三之二、持仓类指标（卖出专用，只对已持有且可卖的仓位计算）

由 `shared/strategy_core.py` 的 `position_metrics()` 生成，回测与 QMT 实盘共用同一份逻辑。

| 字段 | 含义 | 口径 |
|---|---|---|
| `pnl_pct` / `pnl_amount` | 持仓浮盈浮亏 | (最新价 − 成本价) / 成本价；金额按持仓数量 |
| `drawdown_from_high_pct` | 自入场以来最高点的回撤 | (最新价 / 期间最高价 − 1) × 100，回撤时为负 |
| `hold_minutes` | 持有分钟数 | **仅同一交易日内**的 HH:MM 差；隔夜持仓为 `None` |
| `held_qty` / `available_qty` | 持仓数量 / 可卖数量 | 差额即 T+1 锁定量，风控会再次校验 |
| `cost_price` | 成本价 | 来自券商持仓 `m_dOpenPrice` 或回测成本基准 |

**买入与卖出的扫描范围不同**：买入扫全市场（`evaluate_level1_entry` → `evaluate_entry`），卖出只遍历持仓（`evaluate_exit_level1` → `evaluate_exit`）。这样避免把「某只没持仓的股票跌了」误判成卖出信号，也不会对全市场逐只做无意义的持仓计算。

## 四、规则 DSL

规则文件是 UTF-8 JSON，结构：

```json
{
  "version": "1.0.0",
  "name": "示例规则",
  "note": "示例值，非投资建议",
  "mode": "semi_auto",
  "cooldown_minutes": 30,
  "time_windows": [["09:35", "11:25"], ["13:05", "14:50"]],
  "universe": {
    "exclude_st": true,
    "exclude_suspended": true,
    "exclude_limit_up": true,
    "boards": ["主板", "创业板"],
    "min_list_days": 60
  },
  "risk": {
    "max_order_amount": 20000,
    "max_position_pct": 10,
    "max_total_position_pct": 80,
    "max_daily_orders": 20,
    "max_daily_loss_pct": 2,
    "lot_size": 100
  },
  "buy": {
    "logic": "AND",
    "conditions": [
      {"field": "pct_chg", "op": "between", "value": [1.0, 5.0]},
      {"field": "volume_ratio", "op": ">=", "value": 1.5},
      {"field": "vol_x", "op": ">=", "value": 2.0}
    ],
    "precision_conditions": [
      {"field": "ma_bull", "op": "==", "value": true},
      {"field": "macd_hist", "op": ">", "value": 0},
      {"field": "rsi6", "op": "<", "value": 70}
    ]
  },
  "sell": {
    "logic": "OR",
    "conditions": [
      {"field": "pnl_pct", "op": "<=", "value": -3.0},
      {"field": "drawdown_from_high_pct", "op": "<=", "value": -4.0},
      {"field": "pct_chg_5m", "op": "<=", "value": -2.0},
      {"field": "hold_minutes", "op": ">=", "value": 240}
    ],
    "precision_conditions": []
  }
}
```

> `sell.conditions` 只在**有持仓**时求值，可引用快照类 + 持仓类字段。当前生效文件是 `shared/rules/live.json`（v2.0.0），上面是结构示意。

支持的操作符：`>`, `>=`, `<`, `<=`, `==`, `!=`, `between`, `in`, `not_in`。
`logic` 支持 `AND` / `OR`；`between` 的 `value` 是 `[下界, 上界]`（闭区间）。
字段缺失一律判定为**不通过**并记拦截原因，不用 truthy 兜底。

**两级约束**：`conditions`（一级）只能引用快照类字段 —— 引擎在决定是否计算 K 线指标之前就求值它；`precision_conditions`（二级）才引用 K 线类字段。求值时会把两层指标合并，所以二级条件也能看快照字段，反之则不行。

## 五、风控硬前置（下单前顺序，任一不通过即丢弃）

白名单 → 交易时段与时间窗 → 停牌/涨跌停/ST/板块权限 → 可用资金 → 可用持仓与 T+1 → 100 股整数倍与价格笼子 → 单笔金额 / 单票仓位 / 总仓位 / 行业集中度 → 日内委托笔数 / 撤单率 / 日内最大亏损 / 单日成交总额 → 信号幂等与冷却期。

## 六、数据源与口径

- 生产盘中源：浙商证券标准版 QMT 全推行情 `get_full_tick`（全市场 L1 快照，增量约 50ms，二手资料，需以券商文档实测为准）。
- **口径差异（重要）**：QMT 全推不提供标准「量比 / 对 5 日均量的倍数」，`execution/qmt/strategy.py` 用「最近一分钟成交量 ÷ 最近 5 分钟平均分钟成交量」作盘中代理，并在 `data_quality` 里标注；研究层 `vol_x` 用的是 20 bar 均量口径。两者不完全等价，若策略强依赖该指标需在 QMT 实测后统一口径。
- `pct_chg_1m/3m/5m` 在 QMT 里由内存分钟价格序列计算，只保留最近 15 分钟。
- 研究层输入：本地 CSV，字段见 `research/data.py` 的 `BAR_FIELDS`；每行带 `source`、`as_of`、`adjust`。
- iFinD MCP 单次最多 10 标的、仅 L1、不支持全市场扫描，**只做研究校验**。

> **免责声明**：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
