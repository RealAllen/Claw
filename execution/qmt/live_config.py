# -*- coding: utf-8 -*-
"""实盘开关集中配置 —— 导入 QMT 前唯一需要人工修改的文件。

⚠️ 这里不存放、也绝不应该存放交易密码或资金密码。
账户由你在浙商证券 QMT 客户端本机登录后，由客户端加密保管；策略只通过
券商内置沙箱 API 读取账户并下单。ACCOUNT_ID 只是资金账号（非密码），
且在任何日志里都会被脱敏。

启用全自动交易的完整条件见 activation_status()。默认全部关闭，
在收到券商对「程序化交易报告」的确认反馈前，禁止打开下面任何一个开关。
"""
from __future__ import unicode_literals

# ------------------------------------------------------------------ 券商与账户
BROKER_NAME = "浙商证券"

# 资金账号（不是密码）。留空时由 broker_api.detect_account(C) 自动探测。
# 从 QMT 客户端「交易 → 账户信息」或资金账号处取得，填入后请核对 probe() 输出。
ACCOUNT_ID = ""

# get_trade_detail_data 的品种参数。券商定制版常见 "stock" / "STOCK"，以 probe() 为准。
ACCOUNT_TYPE = "stock"

# 全市场代码池的板块名；不同券商定制版名称可能不同，probe() 会逐个尝试。
UNIVERSE_SECTORS = ("沪深A股", "沪深京A股", "全部A股", "A股")

# ------------------------------------------------------------------ 安全开关（默认全关）
KILL_SWITCH = True          # True = 立即停止策略且不再发任何委托
DRY_RUN = True              # True = 只生成订单意图，绝不调用 passorder
MODE = "semi_auto"          # "off" | "semi_auto"（只出信号，人工下单）| "full_auto"（自动下单）
REPORT_CONFIRMED = False    # 是否已收到券商对程序化交易报告的确认反馈（先报告、后交易）
LIVE_ACKNOWLEDGED = False   # 本人二次确认：已读完上线检查表并愿意承担全自动交易后果

# ------------------------------------------------------------------ 运行参数
SCAN_PERIOD = "3nSecond"    # QMT run_time 周期，例如 "3nSecond" / "5nSecond" / "1nMinute"
TIMER_START = "2020-01-01 09:30:00"
MAX_CANDIDATES = 20         # 每轮最多做二级 K 线精算的候选数，防止全市场串行拉 K 线
PRICE_HISTORY_LEN = 12      # 每只标的保留的最近快照数，用于算 1/3/5 分钟涨跌幅
HISTORY_PERIOD = "1d"       # 二级指标用的 K 线周期
HISTORY_COUNT = 80
ORDER_TIMEOUT_SECONDS = 30  # 委托超时未全部成交则撤单
MAX_CANCEL_PER_SCAN = 20

# QMT passorder 参数（迅投 QMT 通用约定，必须以浙商证券官方文档/实测核对）
# passorder(opType, orderType, accountid, orderCode, prType, price, volume,
#           strategyName, quickTrade, userOrderId, ContextInfo)
OP_BUY = 23                 # 23 股票买入 / 24 股票卖出
OP_SELL = 24
ORDER_TYPE = 1101           # 1101 单股单账户按数量委托
PRICE_TYPE = 5              # 5 最新价；若要用限价，按券商文档改 prType 并传 price
QUICK_TRADE = 1
STRATEGY_NAME = "ClawAuto"

# 行情元数据不完整时的策略：False = 用保守默认值并在审计日志里标注；True = 直接跳过该标的。
# 建议首次在 QMT 里跑 probe() 确认 get_instrument_detail 可用后再改为 True。
STRICT_METADATA = False

# ------------------------------------------------------------------ 熔断阈值
MAX_QUOTE_STALE_SECONDS = 90    # 行情断流超过该秒数自动熔断
MAX_REJECTS_PER_SCAN = 10       # 单轮风控拒单过多视为规则/行情异常


def activation_status():
    """返回 (是否允许进入自动交易路径, 未满足的原因列表)。

    注意：semi_auto 也允许通过，但它不会下单（只出信号），由 live_order_allowed() 二次拦截。
    """
    reasons = []
    if MODE not in ("semi_auto", "full_auto"):
        reasons.append("MODE=off")
    if KILL_SWITCH:
        reasons.append("KILL_SWITCH=True")
    if not REPORT_CONFIRMED:
        reasons.append("尚未收到券商对程序化交易报告的确认反馈（先报告、后交易）")
    if not LIVE_ACKNOWLEDGED:
        reasons.append("LIVE_ACKNOWLEDGED=False（未完成本人二次确认）")
    return (len(reasons) == 0), reasons


def live_order_allowed():
    """只有全条件满足才允许真正调用 passorder。"""
    ok, _ = activation_status()
    return bool(ok and MODE == "full_auto" and not DRY_RUN)


def effective_dry_run():
    """传给订单管理器的实际 dry-run 值。任何一道闸门没过，都强制 dry-run。"""
    return not live_order_allowed()


def snapshot():
    """给 QMT 界面/审计用的当前开关画像（不含账号明文）。"""
    ok, reasons = activation_status()
    return {
        "broker": BROKER_NAME,
        "mode": MODE,
        "kill_switch": KILL_SWITCH,
        "dry_run": DRY_RUN,
        "effective_dry_run": effective_dry_run(),
        "report_confirmed": REPORT_CONFIRMED,
        "live_acknowledged": LIVE_ACKNOWLEDGED,
        "live_order_allowed": live_order_allowed(),
        "activation_ok": ok,
        "activation_blockers": reasons,
    }
