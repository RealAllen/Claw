# Claw · 项目交接卡（压缩版）

> **用途**：新会话把本文件贴给 AI，或让它读 `PROJECT_CONTEXT.md`，即可还原全部上下文。
> **更新**：2026-11-08（北京时间）｜**状态**：研究层 / 模拟盘 / QMT 执行层已完成，**实盘未启用**

## 1. 用户原始目标（一句话）

本地部署的 A 股量化平台：用**本人账户**，盘中实时全市场选股 → 逐项分析指标 → 满足自定义阈值后**自动买入 / 卖出**。

## 2. 三项已确认决策（别再重复问）

| 决策点 | 结论 |
|---|---|
| 券商 | **浙商证券，已开通 QMT** |
| 架构 | **接受**：实时决策与下单闭环在券商 QMT 内置 Python 沙箱；自建平台只做盘前配置 / 盘后复盘。两层之间**禁止实时 HTTP / socket / 文件桥接** |
| 交易前端 | **同花顺仅看盘 / 条件单，执行走 QMT**；不要做同花顺 UI 自动化下单 |

合规背景：2026-08-28 中证协 + 中基协《证券公司交易信息系统接入管理规范（试行）》把个人排除在「外接」之外，文件单 / 第三方中转 / 裸 API / 公有云跑单在清退范围；**程序化交易本身未禁**，前提是「先报告、后交易」备案。

## 3. 现在有什么（已完成）

- 位置 `C:\Users\zxs\WorkBuddy\Claw`；PyCharm 2024.3.5 已打开，6 个一键运行配置。
- Git：`https://github.com/RealAllen/Claw`（Public，`main`），3 个提交，最新 `24236a4`；本地 = 远程，工作区干净，**60 个文件**。
- CI `.github/workflows/tests.yml` 两次运行均 **success**；本地 **53 项单元测试全绿**。
- 本地看板 `http://127.0.0.1:8765/`：`config.html` 改指标 / 阈值（只写本地 `shared/rules/live.json`），`index.html` 盘后复盘。
- **QMT 执行层全链路**：`run_time` 定时器（3 秒）→ `get_full_tick` 全市场 L1 → 两级筛选 → 风控硬前置 → 幂等 `client_order_id` → `passorder` → 回报轮询 / 超时撤单 → 对账熔断 → 内存审计。买入扫全市场，**卖出只扫持仓**。
- 指标：涨跌幅 / 1-3-5 分钟涨速 / 振幅 / 量比 / 委比 / 换手 / 资金流；候选再算 MA / MACD / RSI / KDJ / BOLL / 突破；持仓再算盈亏 / 自最高点回撤 / 持有分钟。
- 无券商演练：`python research/cli.py qmt-sim`（内存假 C 对象，可看到完整自动买卖链路）。

## 4. 实盘状态：全关（重要）

`execution/qmt/live_config.py`：`KILL_SWITCH=True`、`DRY_RUN=True`、`MODE=semi_auto`、`REPORT_CONFIRMED=False`、`LIVE_ACKNOWLEDGED=False`、`ACCOUNT_ID=""`。`effective_dry_run()` 保证任何一道闸门没过都不会调用真实 `passorder`。

**至今没有连接券商账户、没有读取或存储密码、没有发出真实委托、没有公网端口、没有安装额外技能 / 连接器。**

## 5. 文件地图（提需求时定位用）

| 想改什么 | 去哪 |
|---|---|
| 指标 / 阈值 / 标的池 / 时间窗 | `shared/rules/live.json`（配置台读写；当前全是**占位示例值**） |
| 规则求值 / 风控 / 幂等 / 卖出判定 | `shared/strategy_core.py`（研究层与 QMT 共用；**改完必须跑 `python research/cli.py export`** 同步沙箱副本并核对哈希） |
| 选股 / 回测 / 绩效 / 看板 | `research/`：`rules_engine.py`、`backtest.py`、`server.py`、`web/` |
| 实盘链路 | `execution/qmt/strategy.py`、`order_manager.py`、`risk_gate.py`、`reconcile.py`、`audit.py` |
| 券商差异 / API 签名 | `execution/qmt/broker_api.py`（只改这里）+ `live_config.py`（参数与闸门） |
| 文档 | `docs/`（合规、指标字典、上线检查表）、`ops/`（运维、应急） |

**硬约束**：QMT 内置 Python 3.6.8 + 库白名单 + 禁文件 / 外网 → 零第三方依赖、3.6 语法、无实时桥接、不写账号密码。

## 6. 还没做完（下次提需求优先看这里）

- **★ 唯一硬阻塞**：浙商证券对「**程序化交易报告**」的**确认反馈**（先报告、后交易）。
- 阶段 0 其余待办：确认是**标准版 QMT 内置 Python 沙箱**（非 miniQMT / 独立进程 / 文件单）、取得券商官方 QMT 安装包、签署程序化交易协议与风险揭示书、实体 Windows 机（非云 / 虚拟机）、向合规部门核实沙箱限制、在 QMT 里跑 `broker_api.probe(C)` 核对 `passorder` / `get_trade_detail_data` 等签名。
- 阶段 1~2：用**真实历史数据**替换演示数据、把占位阈值换成自己的指标要求、样本外 / 压力测试、连续 N 日模拟盘。
- 阶段 3~5：**人工**导出规则进沙箱（禁止自动桥接）、哈希核对、模拟盘全链路、极小资金实盘、运维固化。完整清单见 `docs/GO_LIVE_CHECKLIST.md`。

## 7. 常用命令

```bash
python -m unittest discover -s research/tests -v
python research/cli.py demo          # 生成演示数据
python research/cli.py backtest      # 回测
python research/cli.py scan          # 扫描
python research/cli.py export        # 导出规则 + 内核副本到 QMT 沙箱
python research/cli.py qmt-sim       # 无券商全链路演练
python research/cli.py serve --port 8765
```

## 8. 下次开场可直接粘贴

```
读 C:\Users\zxs\WorkBuddy\Claw\PROJECT_CONTEXT.md 接上 Claw 项目上下文（GitHub RealAllen/Claw，main）。
已确认：浙商证券 QMT 沙箱内闭环、同花顺仅看盘；实盘闸门仍全关。
我这次要做的是：______（例：改指标阈值 / 换真实数据回测 / 补 QMT probe 结果 / 加新指标 / 优化看板）。
约束：零第三方依赖、Python 3.6 语法、禁止实时桥接、不要动实盘开关。
```

## 9. 别踩的坑

- 本地看板**不是**实盘交易界面；实时委托 / 持仓 / 日志只在 QMT 客户端看。
- 示例规则在合成数据上的收益只是**可运行性验证**，不是策略结论，更不是投资建议。
- 别把「代码具备能力」误当成「已开实盘」；别改完 `shared/` 忘了 `export` 导致两端内核哈希不一致。
- 推送若报 502 / schannel：清空 `HTTP(S)_PROXY` 后用 `git -c http.proxy= -c https.proxy= push origin main`（详见 `README.md`）。

> 免责声明：本项目内容为技术研究与量化分析示例，仅供参考，不构成投资建议。市场有风险，投资需谨慎。
