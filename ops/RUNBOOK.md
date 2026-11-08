# 运维手册（RUNBOOK）

## 日常命令

```bash
cd C:\Users\zxs\WorkBuddy\Claw

python -m unittest discover -s research/tests -v      # 回归测试（68 项）
python research/cli.py demo                            # 生成演示数据（仅用于演示）
python research/cli.py backtest                        # 默认读 shared/rules/live.json
python research/cli.py scan
python research/cli.py qmt-sim                         # 无券商演练：完整实时自动买卖链路
python research/cli.py export                          # 生成 QMT 沙箱副本（人工中转）
python research/cli.py serve --host 127.0.0.1 --port 8765
```

配置台：`http://127.0.0.1:8765/config.html`。改完规则保存后必须重新 `export` 并**人工**导入 QMT。

## 每个交易日

| 时点 | 动作 |
|---|---|
| 盘前 | 检查上一交易日收盘对账；确认 `live.json` 版本与 `RULE_DIGEST`；确认 `live_config.activation_status()` 闸门状态；在 QMT 里跑 `probe(C)` / `status(C)` 确认行情源与账户可用 |
| 盘中 | **在 QMT 客户端观察策略日志、委托回报、持仓资金**；调用 `status(C)` 看未确认订单与拒单原因；确认无重复报单；报撤频率留有余量。本地网页不显示实时状态 |
| 盘后 | 导出成交/持仓/资金与影子账本对账；从 QMT 界面导出内存审计日志并归档；跑当日复盘；如有异常先熔断再排查 |
| 定期 | 回归测试；规则变更评审；策略有效性复核（样本外表现、失效条件） |

## 数据

- 研究层读取本地 CSV，字段见 `research/data.py` 的 `BAR_FIELDS`。
- 每行必须含 `source`、`as_of`、`adjust`，禁止硬编码行情数值。
- 演示数据由 `cli.py demo` 生成到 `data/`，**明确标注 DEMO，不得作为生产输入**。
- `data/` 与 `logs/` 已在 `.gitignore` 中排除，不进版本库。

## 版本与发布

1. 规则或策略内核变更 → 先在配置台或 `shared/rules/live.json` / `shared/strategy_core.py`（源头）修改，再跑 `export` 生成沙箱副本。
2. 用哈希核对 `shared/strategy_core.py` 与 QMT 沙箱内副本逐字一致。
3. 任何变更都跑一遍 `unittest` + `backtest`，确认行为差异可解释。
4. 变更后如涉及策略、软件、报单速率、资金规模，按合规要求在次月第 5 个交易日前做变更报告。
5. 采用人工中转导入沙箱，**不得**建立任何实时文件/HTTP/socket 桥接。

## 本地看板安全

- 服务只绑定 `127.0.0.1`；代码里对非回环地址直接拒绝启动。
- 无外部 CDN、无外网请求；研究看板静态打开也能用（显示演示数据），配置台需本地服务。
- 唯一写接口 `POST /api/rules` 固定只写 `shared/rules/live.json`，拒绝账户/凭据/下单类字段、限制请求体大小、原子写入；不建立任何与 QMT 的实时通道。
- 不展示也不存储账号、密码、资金密码；日志脱敏。

## 监控项

信号数、拒单数与拦截原因分布、报单/撤单笔数、对账差异、行情断流次数、策略运行时长、日内亏损占风险预算比例。任一项越界 → 先熔断，再查 `ops/EMERGENCY.md`。
