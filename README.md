# Claw · 本地量化交易平台（浙商证券 QMT 版）

> 零第三方依赖、纯本地、语法兼容 Python 3.6.8。
> **本仓库不连接任何券商账户、不持有交易凭据、不下单**；实时账户操作闭环在券商标准版 QMT 内置沙箱内。

## 这是什么

按 2026-11 的监管与产品现状，「自建本地程序驱动同花顺客户端自动下单」属于被清退的类外接通道且违反同花顺用户协议。因此平台拆成两层，已按你的确认固定为**浙商证券 + 同花顺看盘、QMT 执行**：

| 层 | 位置 | 职责 | 状态 |
|---|---|---|---|
| 研究 / 配置 / 复盘层 | `research/` + `shared/` | 数据、指标、规则配置台、回测、模拟盘、本地看板 | ✅ 已实现 |
| 实时执行层 | `execution/qmt/` | 券商标准版 QMT 内置 Python 沙箱：定时器全推行情 → 全市场选股 → 指标判定 → 风控 → 自动买卖 → 回报/对账/熔断 | ✅ 代码已实现，默认不交易 |

两层之间**只允许盘前 / 盘后人工中转**规则与策略内核源码，**禁止任何实时 HTTP / socket / 文件桥接**。

### 你要的四项能力在哪里

| 目标 | 实现位置 |
|---|---|
| 使用我的账户 | 浙商证券 QMT 客户端本机登录；`broker_api` 读资金/持仓/委托并下单，凭据由券商终端加密保管，代码与日志无密码 |
| 盘中实时选股 | QMT `on_timer` 每 3 秒取 `get_full_tick` 全市场 L1 快照，一级指标粗筛全部标的 |
| 分析各项指标 | 快照指标（涨跌幅/1-3-5 分钟涨速/振幅/量比/委比/换手/资金流）+ 候选池 K 线指标（MA/MACD/RSI/KDJ/BOLL/突破）+ 持仓指标（盈亏/回撤/持有时间） |
| 满足条件自动买卖 | 买入扫全市场、卖出只扫持仓；规则命中 → 风控硬前置 → 幂等 → `passorder` → 回报轮询 → 超时撤单 → 对账 → 熔断 |

盘中实时状态请在 **QMT 客户端**查看（策略日志 / 委托 / 持仓）；本地网页只做盘前配置与盘后复盘。

## 快速开始

```bash
cd C:\Users\zxs\WorkBuddy\Claw

python research/cli.py demo        # 生成明确标注的演示数据
python research/cli.py backtest    # 回测 / 模拟盘
python research/cli.py scan        # 对最新一根 bar 做一次全市场扫描
python research/cli.py qmt-sim     # 无券商演练：跑完整实时自动买卖链路（假账户）
python research/cli.py export      # 导出 QMT 沙箱可粘贴的规则字面量 + 内核副本
python research/cli.py serve       # 启动仅 127.0.0.1 的本地看板

python -m unittest discover -s research/tests -v   # 68 项单元测试
```

`serve` 默认绑定 `127.0.0.1:8765`，打开 `http://127.0.0.1:8765/` 即进入**带左侧功能导航栏的工作台**：

| 导航分组 | 能做什么 |
|---|---|
| 工作台 | 功能总览（全部能力梳理）、策略配置台、模拟盘复盘、最新扫描、能力归属 |
| 研究与演练 | 一键执行：生成演示数据 / 回测 / 扫描 / 无券商演练 / 导出沙箱规则 / 单元测试，输出直接显示在「运行结果」 |
| 执行层 | QMT 执行层状态、六道闸门只读画像、人工导入步骤、安全状态 |
| 文档与合规 | 在线阅读指标字典、上线检查表、合规边界、QMT 接入说明、运维 / 应急手册、交接卡、README |
| 系统 | 运行结果、操作日志（内存，最近 50 条） |

旧地址 `/config.html` 会自动跳转到 `index.html#config`。直接用浏览器打开 `research/web/index.html` 也能看离线演示数据与导航（动作执行、文档阅读、规则保存需要本地服务）。

服务端新增只读 / 白名单接口：`GET /api/features`（导航清单）、`/api/status`、`/api/docs?name=...`、`/api/action-log`，以及 `POST /api/actions`（只允许 6 个固定本地研究命令，无参数、不经过 shell）。**没有任何下单、账户或实时桥接接口。**

## 用 PyCharm 打开（项目已配置好）

根目录带 `Claw.iml` 与 `.idea/`：模块 / source root / test root、Git 映射，以及 6 个预置运行配置。

1. PyCharm → `File > Open` → 选 **`C:\Users\zxs\WorkBuddy\Claw` 这个目录本身**（不要选子目录）→ 勾选 Trust Project。
2. 首次打开若提示配置解释器：`Settings > Project: Claw > Python Interpreter` → 选本机 Python 3.8+（本机为 `C:\Users\zxs\.workbuddy\binaries\python\versions\3.13.12\python.exe`）。**无需 `pip install`**，运行时零第三方依赖。
3. 右上角运行配置下拉，可一键执行：

   | 配置名 | 等价命令 |
   |---|---|
   | `1 全部单元测试 (68)` | `python -m unittest discover -s research/tests -v` |
   | `2 无券商全链路演练` | `python research/cli.py qmt-sim` |
   | `3 生成演示数据` | `python research/cli.py demo` |
   | `4 回测` | `python research/cli.py backtest` |
   | `5 导出沙箱规则` | `python research/cli.py export` |
   | `6 启动本地配置台` | `python research/cli.py serve --port 8765` |

4. `data/` 不入库（演示数据由命令生成），clone 或首次打开后先跑 `3 生成演示数据`，再跑回测 / 扫描。

> `.idea/workspace.xml`、`misc.xml` 等含本机状态的文件已忽略；`Claw.iml`、`modules.xml`、`vcs.xml`、`runConfigurations/` 入库，换机器 clone 后开箱即用。

## Git 与版本管理

- 本地仓库已初始化，默认分支 `main`，初始提交包含 60 个源文件 / 文档 / 配置台 / PyCharm 配置 / CI 文件。
- `.gitignore` 排除 `data/`（演示数据、回测结果）、`logs/`、`.workbuddy/`、所有 `*.secret` / `*.key` / `*.token` / `*_credentials.*` / `.env*`，以及 PyCharm 的本机状态文件。**仓库内不含账号、密码或真实行情数据。**
- `.gitattributes` 统一 LF 行尾。`.github/workflows/tests.yml` 在 push / PR 时对 Python 3.9 / 3.11 / 3.13 跑 68 项单元测试、5 个 CLI 入口冒烟，并用 AST 扫描阻止 Python 3.7+ 语法混入（保证同一份内核可贴进 QMT 的 3.6.8 沙箱）。
- 远程仓库：`https://github.com/RealAllen/Claw`（`origin`，分支 `main`，与本地提交一致）。2026-11-08 完成首次推送，GitHub Actions `tests` 工作流首次运行成功。
- 若 `git push` 报 `CONNECT tunnel failed, 502` 或 `schannel: server closed abruptly`，是会话注入的 `HTTP(S)_PROXY` 阻断了 `github.com` 的 Git 通道。清空 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`（含小写形式）后执行 `git -c http.proxy= -c https.proxy= -c http.sslBackend=openssl push origin main`；首次由 Git Credential Manager 弹窗登录 GitHub，凭据会缓存，后续直接 `git push` 即可。

## 目录

```
shared/
  strategy_core.py          纯函数策略内核（买入/持仓卖出/风控/幂等 ID，3.6 语法，零 IO）
  rules/live.json           唯一生效的生产规则（配置台读写它）
  rules/example_momentum.json  历史示例，不参与默认流程
research/
  indicators.py             快照类 + K 线类指标
  rules_engine.py           规则 DSL 校验与两级筛选编排
  data.py / backtest.py / metrics.py
  server.py                 仅 127.0.0.1 的 HTTP 服务 + 功能清单 / 白名单动作 / 文档 / 状态 API
  cli.py                    统一入口
  web/                      左侧导航工作台：index.html + nav.js + app.js + config.js + style.css
  tests/                    68 项 unittest（含工作台接口与安全边界）
execution/qmt/
  live_config.py            ★ 唯一需要人工修改的开关文件（账号/模式/4 道闸门/API 参数）
  broker_api.py             ★ 券商 API 适配层（所有因券商而异的签名）+ probe() 诊断
  strategy.py               init/on_timer/handlebar/scan_once/poll_orders/status/set_kill_switch
  risk_gate.py / order_manager.py / reconcile.py / audit.py
  rules_snapshot.py / strategy_core.py / indicators.py   ← export 生成的沙箱副本
tools/
  export_rules.py           JSON → Python 字面量 + 复制内核（人工中转用）
  qmt_sim.py                无券商演练：假 ContextInfo 跑完整实时回路
docs/ ops/                  合规清单、指标字典、上线检查表、运维与应急手册
```

## 设计约束（不可协商）

1. **零第三方依赖 + Python 3.6 语法**：同一份 `strategy_core.py` 既跑本机 Python 3.13 研究，也直接粘贴进 QMT 内置沙箱。
2. **不接触凭据**：仓库无账号/密码/资金密码字段；`live_config.ACCOUNT_ID` 只是资金账号，日志脱敏。
3. **只读研究层**：研究层写操作只限于保存本地规则 JSON（`POST /api/rules`）与执行 6 个固定白名单本地研究命令（`POST /api/actions`），不含任何下单能力、不监听公网、不与 QMT 建实时通道。
4. **执行层默认不交易**：`KILL_SWITCH=True`、`DRY_RUN=True`、`MODE=semi_auto`、`REPORT_CONFIRMED=False`、`LIVE_ACKNOWLEDGED=False`；`activation_status()` 任一不满足即强制 dry-run。
5. **演示数据不是生产数据**：`demo` 生成确定性合成数据，页面与结果都标注 `DEMO`。

## 已验证（2026-11-07，Windows 11 / Python 3.13.14）

| 验证项 | 结果 |
|---|---|
| 单元测试 | `python -m unittest discover -s research/tests -v` → **68 项全部通过**（含自动买卖、半自动、持仓卖出、幂等、超时撤单、Kill Switch、账号脱敏） |
| 无券商演练 | `qmt-sim`：3 个时点扫描、7 笔模拟委托（6 买 1 卖）、成交回报更新持仓与 T+1、定时器注册 `on_timer/3nSecond`，全部落在内存假对象 |
| 回测 / 扫描 | 4320 行演示数据；116 笔成交信号、28 笔拦截；示例规则总收益 +0.7695%（**合成数据 + 未优化阈值，不是策略结论**） |
| 规则导出 | `rules_snapshot.py` 指纹 `32d1ca1e…`；内核与指标副本 SHA-256 与源文件一致 |
| 本地服务 | `/api/health`、`/api/dashboard`、`/api/rules`、`/api/capabilities`、`/api/features`、`/api/status`、`/api/docs`、`/api/actions`、`/api/action-log`、静态页均 200；目录穿越 / 未知文档 / 未知动作被拒；非白名单 POST 405；含 `account_id` 的规则保存 400；非回环绑定被拒绝 |
| 语法 | `node --check` 通过；30 个 Python 文件静态扫描无 3.7+ 专有语法 |
| 安全默认 | 应用内无账户凭据；四道闸门全部关闭；演练结束自动恢复 |

## 还没做 / 需要你确认

**唯一硬阻塞**：浙商证券对「程序化交易报告」的确认反馈（先报告、后交易）。另外需在 QMT 里核对 `broker_api.probe(C)` 输出的 API 签名与浙商证券官方文档。其余 6 项决策点见 `docs/GO_LIVE_CHECKLIST.md`。

在此之前：`execution/qmt/` 代码完整可跑，但**不会发出任何真实委托**；`qmt-sim` 与全部测试都在假对象上运行。

> **免责声明**：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
