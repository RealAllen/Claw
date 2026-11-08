# QMT 沙箱执行层 · 盘中实时选股与自动买卖（浙商证券标准版 QMT）

> ⚠️ **默认不交易**：`KILL_SWITCH=True`、`DRY_RUN=True`、`MODE=semi_auto`、`REPORT_CONFIRMED=False`。
> 代码已具备完整自动买卖能力，但**在收到券商对程序化交易报告的确认反馈前，禁止打开实盘开关**。

## 一、你要的四项能力，在这里全部落地

| 目标 | 本目录的实现 |
|---|---|
| 使用我的账户 | `broker_api.query_account / query_positions / query_orders` 读取资金、持仓、委托；`passorder` 下单。账户由 QMT 客户端本机登录并加密保管凭据，代码与日志里**没有也不需要交易密码/资金密码**，账号在日志中一律脱敏 |
| 盘中实时选股 | `init()` 用 `run_time("on_timer", "3nSecond", ...)` 注册定时器；`on_timer` 每 3 秒取 `get_full_tick` 全市场 L1 快照，一级快照指标粗筛全部标的 |
| 分析各项指标 | `strategy._normalize_tick` 算涨跌幅、1/3/5 分钟涨速、振幅、量比/放量代理、委比、换手、资金流；`indicators.precision_metrics` 对候选池算 MA/MACD/RSI/KDJ/BOLL/突破；`strategy_core.position_metrics` 算持仓盈亏、自最高点回撤、持有分钟 |
| 满足条件自动买卖 | 买入扫全市场、卖出**只扫已持有且可卖的仓位**；规则命中 → 风控硬前置 → 幂等 `client_order_id` → `passorder` → 回报轮询/超时撤单 → 影子账本 → 对账熔断 → 内存审计 |

**实时日志、委托回报、持仓资金请在浙商证券 QMT 客户端查看**；本地 Web 看板只做盘前配置与盘后复盘，不参与实时回路。

## 二、文件清单（整体复制进 QMT 内置策略目录）

| 文件 | 作用 |
|---|---|
| `live_config.py` | **唯一需要人工修改的文件**：账号、模式、4 道安全闸门、定时器周期、passorder 参数、熔断阈值 |
| `broker_api.py` | 券商 API 适配层：行情/账户/持仓/委托/下单/撤单/定时器/上市天数/`probe()` 诊断。所有因券商而异的签名只在这里 |
| `strategy.py` | 主策略：`init` / `on_timer` / `handlebar` / `scan_once` / `poll_orders` / `check_reconciliation` / `set_kill_switch` / `status` |
| `rules_snapshot.py` | `python research/cli.py export` 从 `shared/rules/live.json` 生成的 Python 字面量（**不读文件**） |
| `strategy_core.py` | 与研究层逐字一致的策略内核（哈希核对） |
| `indicators.py` | 与研究层逐字一致的指标实现（哈希核对） |
| `risk_gate.py` / `order_manager.py` / `reconcile.py` / `audit.py` | 风控硬前置 / 订单状态机 / 对账 / 内存审计 |

## 三、导入步骤（盘前人工中转，禁止实时桥接）

```bash
cd C:\Users\zxs\WorkBuddy\Claw
python -m unittest discover -s research/tests -v
python research/cli.py qmt-sim            # 无券商演练，先看链路
python research/cli.py export             # 生成 rules_snapshot.py + 复制内核/指标
```

1. 核对 `rules_snapshot.py` 顶部 `RULE_DIGEST` 与 `CORE_SHA256`。
2. **人工**把本目录所有 `.py` 复制/粘贴进浙商证券 QMT 客户端自带 Python 编辑器的策略目录。
3. 在 QMT 里核对 `shared/strategy_core.py` 与沙箱内副本的 SHA-256 一致。
4. 在 QMT 里启动策略，先跑 `status(C)` / `probe(C)`，把输出与券商官方 QMT 文档逐项对照。
5. 规则变更 → 重新 `export` → 重新人工导入。**不要搭任何自动同步**。

## 四、首次运行必须做的 API 核对（`probe(C)`）

迅投 QMT 的通用约定可能与浙商证券定制版不同。以下都在 `broker_api.py` 里集中实现，核对后只改这一个文件：

- [ ] `passorder(opType, orderType, accountid, orderCode, prType, price, volume, strategyName, quickTrade, userOrderId, C)` 的参数顺序与 `OP_BUY/OP_SELL/ORDER_TYPE/PRICE_TYPE` 取值
- [ ] `get_trade_detail_data(accountid, 'stock', 'account'|'position'|'order')` 的品种参数与返回字段（`m_dAvailable` / `m_nCanUseVolume` / `m_dOpenPrice` / `m_strOrderSysID` / 委托备注里的幂等 ID）
- [ ] `get_full_tick(stock_list)` 的字段名（`lastPrice` / `lastClose` / `bidVol` / `askVol` / `stockStatus` 等）
- [ ] `get_market_data_ex` 的返回结构（本适配层同时兼容 `{code: DataFrame}`、`{field: DataFrame}`、list[dict]）
- [ ] `get_stock_list_in_sector` 的板块名（依次尝试 `沪深A股` / `沪深京A股` / `全部A股` / `A股`）
- [ ] `get_instrument_detail(code)` 是否有 `OpenDate`；没有时 `list_days` 过滤会被显式标注为失效
- [ ] `run_time(func, '3nSecond', start_time)` 的周期写法
- [ ] `cancel(orderId, accountId, accountType, C)` 的撤单签名
- [ ] 资金账号 `ACCOUNT_ID` 是否能自动探测；探测不到就手工填入（**只填资金账号，不填任何密码**）

## 五、启用全自动交易（五道闸门，缺一不可）

在 `live_config.py` 中按顺序修改，每一步都要留人工记录：

| 步骤 | 改动 | 前置条件 |
|---|---|---|
| 1 | `ACCOUNT_ID` 填入资金账号；跑 `probe(C)` 确认 API 全部可用 | 已拿到浙商证券官方 QMT 安装包，实体 Windows 机 |
| 2 | `REPORT_CONFIRMED = True` | **已收到券商对程序化交易报告的确认反馈**（先报告、后交易） |
| 3 | `LIVE_ACKNOWLEDGED = True` | 本人已读完 `docs/GO_LIVE_CHECKLIST.md` 并确认自担风险 |
| 4 | `MODE = "semi_auto"` → `"full_auto"` | 模拟盘连续 N 日零误触发；极小资金实盘 1~2 周对账零差异 |
| 5 | `KILL_SWITCH = False`、`DRY_RUN = False` | 熔断/断网/崩溃演练通过；`status(C)` 显示 `live_order_allowed=True` |

`live_config.activation_status()` 会把未满足的闸门逐条列出来；`effective_dry_run()` 保证**任何一道没过都强制 dry-run**。半自动模式只生成信号、不调用 `passorder`，由你人工下单。

## 六、沙箱内硬约束（均已遵守）

- 无 `open()` / 文件读写；审计日志只存内存，由 QMT 界面人工导出
- 无 `requests` / `socket` / HTTP / TCP / UDP / 数据库 / 外部 IDE
- 无实时桥接：外部进程不生成、不转发任何交易指令
- 不在公有云、虚拟机、云桌面运行
- Python 3.6.8 语法，仅标准库

## 七、盘中监控与应急

- 在 QMT 里调用 `strategy.status(C)` 可得到模式、闸门、持仓、当日委托、未确认订单、规则指纹、最近信号/拒单。
- `strategy.set_kill_switch(C, True, "原因")` 或直接在 QMT 界面停止策略 → 立即不再发委托；随后撤未成交单、对账。
- 自动熔断：行情断流 >90s、对账不一致、撤单失败、当日亏损触线、日内委托笔数超限。
- 见 `ops/EMERGENCY.md`、`docs/GO_LIVE_CHECKLIST.md`。

## 八、已知边界（务必知道）

1. **`volume_ratio` / `vol_x` 是盘中分钟放量代理**：QMT 全推不直接给「量比 / 对 5 日均量的倍数」，策略用「最近一分钟成交量 ÷ 最近 5 分钟平均分钟成交量」近似，并在 `data_quality` 里标注。研究层 `indicators.snapshot_metrics` 用的是另一套（20 bar 均量）口径，两者不完全等价 —— 若策略依赖这个指标，请在 QMT 实测后决定是否改成同口径。
2. **`list_days` 依赖 `get_instrument_detail`**：取不到时 `STRICT_METADATA=False` 会用 `99999` 占位并标注「上市天数过滤失效」；建议确认接口可用后改为 `True`（缺失则直接跳过该标的）。
3. **`hold_minutes` 只算同一交易日内的分钟差**；隔夜持仓返回 `None`，依赖该字段的卖出条件不会触发，但盈亏/回撤类条件照常工作。
4. 全推约 50ms、内置 Python 版本、订阅上限等来自二手资料，**以浙商证券官方文档与实测为准**。
5. 本目录代码在普通 Python 里用假 `C` 对象也能跑（68 项测试 + `qmt-sim` 演练），但那只是链路验证，**不代表券商接口签名已核对**。

> **免责声明**：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
