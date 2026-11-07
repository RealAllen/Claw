# -*- coding: utf-8 -*-
"""浙商证券标准版 QMT 内置 Python 沙箱 · 盘中实时选股与自动买卖策略。

实现的能力（对应用户原始目标）
------------------------------
1. 使用本人账户：账户由 QMT 客户端本机登录并保管凭据，策略只通过沙箱 API
   读取资金/持仓/委托并下单；代码与日志里没有、也不需要交易密码或资金密码。
2. 盘中实时选股：定时器每 SCAN_PERIOD 触发一次，用全推行情 get_full_tick
   扫全市场 L1 快照，一级快照指标粗筛，只对候选池算 K 线类指标。
3. 分析各项指标：涨跌幅、1/3/5 分钟涨跌幅、振幅、量比/放量代理、委比、
   换手、资金流，以及 MA/MACD/RSI/KDJ/BOLL/突破等二级指标与持仓盈亏指标。
4. 满足条件自动买卖：买入信号来自全市场扫描，卖出信号只针对已持有且可卖
   的仓位（止盈/止损/追踪回撤/持有时间/盘中反转）；规则命中 -> 风控硬前置
   -> 幂等去重 -> passorder -> 回报轮询/超时撤单 -> 对账 -> 内存审计。

合规边界
--------
实时决策、风控、下单全部闭环在券商沙箱内。本文件禁止 open()/文件读写、
socket/HTTP/数据库、外部 IDE、任何实时桥接；审计日志只存内存，由 QMT 界面
人工导出。本地自建研究层只在盘前提供规则、盘后做复盘，不参与实时回路。

默认 KILL_SWITCH=True、DRY_RUN=True、MODE=semi_auto、REPORT_CONFIRMED=False，
不会发出任何真实委托。启用步骤见 execution/qmt/README.md 与上线检查表。

Python 3.6.8 语法，零第三方依赖。所有券商专属签名隔离在 broker_api.py。
"""
from __future__ import division

try:
    from . import strategy_core as core
    from . import indicators as ind
    from . import risk_gate, order_manager, reconcile, audit, broker_api, live_config as cfg
except (ImportError, ValueError):
    import strategy_core as core
    import indicators as ind
    import risk_gate
    import order_manager
    import reconcile
    import audit
    import broker_api
    import live_config as cfg

try:
    from . import rules_snapshot as snap
except (ImportError, ValueError):
    try:
        import rules_snapshot as snap
    except ImportError:
        snap = None

RULES = getattr(snap, "RULES", {}) if snap is not None else {}
RULE_DIGEST = getattr(snap, "RULE_DIGEST", "missing") if snap is not None else "missing"

# ---------------------------------------------------------------- 安全开关（可被测试/运行时覆盖）
KILL_SWITCH = cfg.KILL_SWITCH
DRY_RUN = cfg.DRY_RUN
MODE = cfg.MODE

_CURRENT_DATE = ""
_CURRENT_HHMM = "10:30"
_NOW_TS = 0.0
_SCANNING = False

UNKNOWN_LIST_DAYS = 99999  # 元数据缺失时的保守占位，仅在 STRICT_METADATA=False 时使用


# ---------------------------------------------------------------- 工具
def _hhmm_minus(hhmm, minutes):
    base = core.to_minutes(hhmm)
    if base is None:
        return None
    value = base - minutes
    if value < 0:
        return None
    return "%02d:%02d" % (value // 60, value % 60)


def _prune_minute_map(mapping, hhmm, keep=15):
    """只保留最近 keep 分钟，防止内存无限增长。"""
    now = core.to_minutes(hhmm)
    if now is None:
        return
    for key in list(mapping.keys()):
        point = core.to_minutes(key)
        if point is None or point < now - keep:
            del mapping[key]


def _sum_vol(value):
    if isinstance(value, (list, tuple)):
        total = 0.0
        for item in value:
            try:
                total += float(item)
            except (TypeError, ValueError):
                continue
        return total
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _board_of(code):
    head = str(code).split(".")[0]
    if head.startswith(("300", "301")):
        return "创业板"
    if head.startswith(("688", "689")):
        return "科创板"
    if head and head[0] in ("4", "8"):
        return "北交所"
    return "主板"


def _normalize_tick(C, code, tick, state, hhmm, date_str):
    """把 QMT 全推快照转成策略内核的指标 dict，并维护盘中分钟序列。"""
    tick = tick or {}

    def pick(*names):
        for name in names:
            if name in tick and tick[name] is not None:
                return tick[name]
        return None

    last = pick("lastPrice", "last", "close", "price")
    prev = pick("lastClose", "preClose", "prev_close", "last_close")
    high = pick("high", "highPrice")
    low = pick("low", "lowPrice")
    open_price = pick("open", "openPrice")
    volume = pick("volume", "vol")
    amount = pick("amount", "turnover")
    notes = []

    minutes = state["minute_price"].setdefault(code, {})
    cumvol = state["minute_volume"].setdefault(code, {})
    if last is not None:
        minutes[hhmm] = float(last)
    if volume is not None:
        cumvol[hhmm] = float(volume)
    _prune_minute_map(minutes, hhmm)
    _prune_minute_map(cumvol, hhmm)

    def change_n(n):
        past = minutes.get(_hhmm_minus(hhmm, n))
        if past and last is not None:
            return round((float(last) / float(past) - 1.0) * 100.0, 4)
        return None

    # 放量代理：最近一分钟成交量 / 最近 5 分钟平均分钟成交量。
    # QMT 全推不直接给「量比 / 对 5 日均量的倍数」，这里用盘中分钟增量近似，
    # 与研究层 indicators.snapshot_metrics 的 vol_x 口径不同，已在 notes 里标注。
    vol_x = pick("vol_x", "volX")
    volume_ratio = pick("volumeRatio", "volume_ratio")
    deltas = []
    for n in range(0, 5):
        cur = cumvol.get(_hhmm_minus(hhmm, n))
        older = cumvol.get(_hhmm_minus(hhmm, n + 1))
        if cur is not None and older is not None:
            deltas.append(max(0.0, cur - older))
    if deltas and len(deltas) >= 2:
        baseline = sum(deltas[1:]) / float(len(deltas) - 1)
        if baseline > 0:
            intraday_vol_x = round(deltas[0] / baseline, 4)
            if vol_x is None:
                vol_x = intraday_vol_x
                notes.append("vol_x=intraday_minute_proxy")
            if volume_ratio is None:
                volume_ratio = intraday_vol_x
                notes.append("volume_ratio=intraday_minute_proxy")

    bid_vol = _sum_vol(pick("bidVol", "bidVolume", "bid_vol"))
    ask_vol = _sum_vol(pick("askVol", "askVolume", "ask_vol"))
    bid_ask_ratio = pick("bidAskRatio", "bid_ask_ratio")
    if bid_ask_ratio is None and bid_vol is not None and ask_vol is not None:
        total = bid_vol + ask_vol
        if total > 0:
            bid_ask_ratio = round((bid_vol - ask_vol) / total * 100.0, 4)

    pct = None
    if last is not None and prev:
        pct = (float(last) / float(prev) - 1.0) * 100.0
    amplitude = None
    if high is not None and low is not None and prev:
        amplitude = round((float(high) - float(low)) / float(prev) * 100.0, 4)

    board = pick("board", "market") or _board_of(code)
    limit = ind.BOARD_LIMIT.get(board, 10.0)
    name = str(pick("instrumentName", "name", "stockName") or "")
    status = str(pick("stockStatus", "status") or "")
    is_st = bool(pick("isST", "is_st") or ("ST" in name.upper()))
    suspended = bool(pick("suspended") or ("停" in status))

    list_days = pick("listDays", "list_days")
    if list_days is None:
        list_days = broker_api.list_days(C, code)
    if list_days is None:
        if cfg.STRICT_METADATA:
            return None
        list_days = UNKNOWN_LIST_DAYS
        notes.append("list_days_unavailable:filter_disabled")

    high_limit = pick("highLimit", "limitUp")
    low_limit = pick("lowLimit", "limitDown")
    if high_limit is None:
        high_limit = bool(pct is not None and pct >= limit - 0.3)
    if low_limit is None:
        low_limit = bool(pct is not None and pct <= -(limit - 0.3))

    return {
        "symbol": code,
        "date": date_str,
        "hhmm": hhmm,
        "as_of": "%s %s" % (date_str, hhmm),
        "last": float(last) if last is not None else None,
        "prev_close": float(prev) if prev is not None else None,
        "open": float(open_price) if open_price is not None else None,
        "high": float(high) if high is not None else None,
        "low": float(low) if low is not None else None,
        "volume": float(volume) if volume is not None else None,
        "amount": float(amount) if amount is not None else None,
        "pct_chg": round(pct, 4) if pct is not None else None,
        "pct_chg_1m": change_n(1),
        "pct_chg_3m": change_n(3),
        "pct_chg_5m": change_n(5),
        "amplitude": amplitude,
        "vol_x": vol_x,
        "volume_ratio": volume_ratio,
        "turnover_rate": pick("turnoverRate", "turnover_rate"),
        "bid_ask_ratio": bid_ask_ratio,
        "net_inflow": pick("netInflow", "net_inflow"),
        "board": board,
        "is_st": is_st,
        "suspended": suspended,
        "list_days": list_days,
        "high_limit": bool(high_limit),
        "low_limit": bool(low_limit),
        "source": "QMT_FULL_TICK",
        "adjust": "none",
        "data_quality": notes,
    }


def _register_qmt_globals():
    """把 QMT 注入到策略命名空间的全局函数注册进适配层。"""
    namespace = globals()
    builtin = namespace.get("__builtins__")
    for name, setter in (("passorder", broker_api.register_passorder),
                         ("cancel", broker_api.register_cancel)):
        fn = namespace.get(name)
        if fn is None and isinstance(builtin, dict):
            fn = builtin.get(name)
        elif fn is None and builtin is not None:
            fn = getattr(builtin, name, None)
        if callable(fn):
            setter(fn)


# ---------------------------------------------------------------- 生命周期
def init(C):
    """QMT 策略初始化：注册 API、探测券商环境、建内存状态、启动定时器。"""
    _register_qmt_globals()
    account_id = broker_api.detect_account(C)
    account = broker_api.query_account(C, account_id)
    positions = broker_api.query_positions(C, account_id)
    cash = float(account.get("cash", getattr(C, "cash", 0.0) or 0.0) or 0.0)

    state = {
        "audit": audit.AuditLog(capacity=5000),
        "orders": order_manager.OrderManager(
            dry_run=(DRY_RUN or cfg.effective_dry_run()),
            kill_switch=KILL_SWITCH),
        "account_id": account_id,
        "account": account or {},
        "positions": positions,
        "position_cost": dict((k, v.get("cost")) for k, v in positions.items() if v.get("cost")),
        "entry_hhmm": {},
        "high_since_entry": {},
        "minute_price": {},
        "minute_volume": {},
        "ledger_positions": dict((k, v["qty"]) for k, v in positions.items()),
        "ledger_cash": cash,
        "last_signal_minutes": {},
        "seen_ids": [],
        "day_orders": 0,
        "day_turnover": 0.0,
        "day_loss_pct": 0.0,
        "current_date": "",
        "last_quote_ts": 0.0,
        "halt": False,
        "rejects_this_scan": 0,
        "rule_digest": RULE_DIGEST,
        "probe": broker_api.probe(C),
        "timer_started": False,
        "whole_quote_subscribed": False,
    }
    C.claw_state = state
    state["whole_quote_subscribed"] = broker_api.subscribe_whole_quote(C)
    state["timer_started"] = broker_api.start_timer(C, "on_timer")
    state["audit"].append("init", {
        "config": cfg.snapshot(),
        "probe": state["probe"],
        "timer_started": state["timer_started"],
        "whole_quote_subscribed": state["whole_quote_subscribed"],
        "rule_digest": RULE_DIGEST,
    }, "")
    return state


def refresh_account(C, state):
    """每轮扫描前刷新资金、持仓与持仓成本（券商数据为准）。"""
    account = broker_api.query_account(C, state["account_id"]) or {}
    positions = broker_api.query_positions(C, state["account_id"])
    state["account"] = account
    state["positions"] = positions
    for code, pos in positions.items():
        if pos.get("cost") is not None:
            state["position_cost"][code] = pos["cost"]
        last = pos.get("last")
        if last:
            state["high_since_entry"][code] = max(
                state["high_since_entry"].get(code, float(last)), float(last))


def _account(C, state):
    """把券商账户 + 本地当日账本合成风控所需 dict。"""
    account = state.get("account") or {}
    positions = {}
    prices = {}
    t1_locked = {}
    for code, pos in state["positions"].items():
        qty = float(pos.get("qty", 0) or 0)
        positions[code] = qty
        prices[code] = float(pos.get("last") or 0.0)
        t1_locked[code] = max(0.0, qty - float(pos.get("available", qty) or 0))
    equity = float(account.get("total_equity", 0.0) or 0.0)
    if equity <= 0:
        equity = float(account.get("cash", state["ledger_cash"]) or 0.0) + sum(
            qty * prices.get(code, 0.0) for code, qty in positions.items())
    return {
        "cash": float(account.get("cash", state["ledger_cash"]) or 0.0),
        "total_equity": equity,
        "positions": positions,
        "position_prices": prices,
        "day_orders": state["day_orders"],
        "day_turnover": state["day_turnover"],
        "day_loss_pct": state["day_loss_pct"],
        "seen_ids": state["seen_ids"],
        "last_signal_minutes": state["last_signal_minutes"],
        "t1_locked": t1_locked,
        "kill_switch": KILL_SWITCH or state["halt"],
    }


def _process_signal(C, state, signal, snapshot, reasons):
    """风控硬前置 -> 幂等下单 -> 账本更新。返回 [order]。"""
    log = state["audit"]
    hhmm = snapshot.get("hhmm") or _CURRENT_HHMM
    account = _account(C, state)
    signal["qty"] = risk_gate.size_order(signal["side"], signal.get("price"),
                                         signal.get("symbol"), account, RULES)
    ok, risk_reasons = risk_gate.precheck(signal, account, RULES, hhmm)
    if not ok:
        log.append("risk_reject", {"symbol": signal.get("symbol"),
                                   "side": signal.get("side"),
                                   "reasons": risk_reasons}, snapshot.get("as_of", ""))
        state["rejects_this_scan"] += 1
        return []

    order, outcome = state["orders"].submit(signal, C, snapshot.get("as_of", ""),
                                            now_ts=_NOW_TS)
    log.append("signal", {
        "symbol": signal.get("symbol"),
        "side": signal.get("side"),
        "id": signal["client_order_id"],
        "price": signal.get("price"),
        "qty": signal.get("qty"),
        "outcome": outcome,
        "metrics": dict(signal.get("metrics") or {}),
        "reasons": reasons[-6:],
    }, snapshot.get("as_of", ""))
    if order is None:
        return []

    state["seen_ids"].append(signal["client_order_id"])
    minutes = core.to_minutes(hhmm)
    if minutes is not None:
        state["last_signal_minutes"][signal["symbol"]] = minutes
    state["day_orders"] += 1
    state["day_turnover"] += (signal.get("price") or 0) * signal["qty"]
    last = snapshot.get("last")
    if last:
        state["high_since_entry"][signal["symbol"]] = max(
            state["high_since_entry"].get(signal["symbol"], float(last)), float(last))
    if signal["side"] == "buy":
        state["entry_hhmm"].setdefault(signal["symbol"], hhmm)
    return [order]


def _position_dict(state, code, pos):
    return {
        "qty": float(pos.get("qty", 0) or 0),
        "available": float(pos.get("available", 0) or 0),
        "cost": pos.get("cost") if pos.get("cost") is not None else state["position_cost"].get(code),
        "entry_hhmm": state["entry_hhmm"].get(code),
        "high_since_entry": state["high_since_entry"].get(code),
    }


def poll_orders(C, state, as_of):
    """委托回报轮询 + 超时撤单；撤单失败立即熔断。"""
    log = state["audit"]
    for row in broker_api.query_orders(C, state["account_id"]):
        cid = row.get("client_order_id")
        if not cid or state["orders"].get(cid) is None:
            continue
        before = state["orders"].get(cid)["state"]
        state["orders"].on_report(cid, row["state"], as_of, row.get("filled_qty"))
        if row["state"] in ("filled", "partial") and before != row["state"]:
            _apply_fill(state, cid, row.get("filled_qty"))
    for order in state["orders"].timed_out(_NOW_TS, cfg.ORDER_TIMEOUT_SECONDS):
        if broker_api.cancel_order(C, order.get("broker_order_id")):
            state["orders"].on_report(order["client_order_id"], "cancelled", as_of)
            log.append("timeout_cancel", {"id": order["client_order_id"]}, as_of)
        else:
            state["halt"] = True
            log.append("cancel_failed_halt", {"id": order["client_order_id"]}, as_of)
            break


def _apply_fill(state, cid, filled_qty):
    """按成交回报更新本地影子账本，供盘后对账使用（非券商账本）。"""
    order = state["orders"].get(cid)
    if order is None:
        return
    qty = float(filled_qty if filled_qty is not None else (order.get("qty") or 0))
    price = float(order.get("price") or 0)
    code = order.get("symbol")
    if not code or qty <= 0:
        return
    if order.get("side") == "buy":
        state["ledger_cash"] -= price * qty
        state["ledger_positions"][code] = state["ledger_positions"].get(code, 0) + qty
    else:
        state["ledger_cash"] += price * qty
        state["ledger_positions"][code] = max(0, state["ledger_positions"].get(code, 0) - qty)


# ---------------------------------------------------------------- 主扫描
def scan_once(C, state, date_str="", hhmm="10:30", now_ts=None):
    """单轮实时扫描：全推行情 -> 两级筛选 -> 风控 -> 幂等下单 -> 回报轮询。"""
    global _CURRENT_DATE, _CURRENT_HHMM, _NOW_TS, _SCANNING
    _CURRENT_DATE, _CURRENT_HHMM = date_str, hhmm
    _NOW_TS = float(now_ts) if now_ts is not None else 0.0
    log = state["audit"]

    if KILL_SWITCH or state["halt"] or MODE == "off":
        log.append("halted", {"reason": "kill_switch_or_halt_or_off",
                             "kill_switch": KILL_SWITCH,
                             "halt": state["halt"], "mode": MODE},
                   "%s %s" % (date_str, hhmm))
        return []
    if _SCANNING:
        return []
    _SCANNING = True
    try:
        if date_str and state["current_date"] != date_str:
            state["current_date"] = date_str
            state["day_orders"] = 0
            state["day_turnover"] = 0.0
            state["day_loss_pct"] = 0.0
            state["last_signal_minutes"] = {}
            state["seen_ids"] = []
            state["minute_price"] = {}
            state["minute_volume"] = {}

        ok_time, _ = core.in_time_window(hhmm, RULES.get("time_windows"))
        if not ok_time:
            log.append("outside_window", {"hhmm": hhmm}, "%s %s" % (date_str, hhmm))
            return []

        state["rejects_this_scan"] = 0
        refresh_account(C, state)

        codes = broker_api.get_stock_list(C)
        raw_ticks = broker_api.get_full_tick(C, codes) or {}
        tick_map = {}
        for key, value in raw_ticks.items():
            tick_map[broker_api._normalize_code(key)] = value
        if not tick_map:
            stale = _NOW_TS - state["last_quote_ts"] if state["last_quote_ts"] else 0.0
            if state["last_quote_ts"] and stale > cfg.MAX_QUOTE_STALE_SECONDS:
                state["halt"] = True
                log.append("quote_stale_halt", {"stale_seconds": stale},
                           "%s %s" % (date_str, hhmm))
            else:
                log.append("quote_empty", {"stale_seconds": stale},
                           "%s %s" % (date_str, hhmm))
            return []
        state["last_quote_ts"] = _NOW_TS

        results = []

        # ---- 买入：全市场一级粗筛，只对候选池做 K 线精算
        candidates = []
        for code in sorted(tick_map.keys()):
            snapshot = _normalize_tick(C, code, tick_map[code], state, hhmm, date_str)
            if snapshot is None:
                continue
            ok_universe, _ = core.check_universe(snapshot, RULES.get("universe", {}))
            if not ok_universe:
                continue
            ok1, _ = core.evaluate_level1_entry(snapshot, RULES)
            if ok1:
                candidates.append((code, snapshot))
        candidates.sort(key=lambda item: item[1].get("pct_chg") or 0.0, reverse=True)
        for code, snapshot in candidates[:cfg.MAX_CANDIDATES]:
            bars = broker_api.get_history(C, code)
            precision = ind.precision_metrics(bars) if bars else {}
            signal, reasons = core.evaluate_entry(code, snapshot, precision, RULES)
            if signal is None:
                log.append("precision_reject", {"symbol": code, "reasons": reasons[-4:]},
                           snapshot["as_of"])
                continue
            results.extend(_process_signal(C, state, signal, snapshot, reasons))

        # ---- 卖出：只扫描已持有且可卖的仓位，不把全市场快照误判成卖出信号
        for code, pos in sorted(state["positions"].items()):
            if float(pos.get("available", 0) or 0) <= 0:
                continue
            snapshot = _normalize_tick(C, code, tick_map.get(code), state, hhmm, date_str)
            if snapshot is None or snapshot.get("last") is None:
                continue
            position = _position_dict(state, code, pos)
            ok1, _ = core.evaluate_exit_level1(snapshot, position, RULES, hhmm)
            if not ok1:
                continue
            bars = broker_api.get_history(C, code)
            precision = ind.precision_metrics(bars) if bars else {}
            signal, reasons = core.evaluate_exit(code, snapshot, precision,
                                                 position, RULES, hhmm)
            if signal is None:
                continue
            results.extend(_process_signal(C, state, signal, snapshot, reasons))

        poll_orders(C, state, "%s %s" % (date_str, hhmm))

        if state["rejects_this_scan"] > cfg.MAX_REJECTS_PER_SCAN:
            log.append("reject_spike", {"count": state["rejects_this_scan"]},
                       "%s %s" % (date_str, hhmm))
        log.append("scan", {"universe": len(tick_map),
                            "candidates": len(candidates),
                            "orders": len(results),
                            "rejects": state["rejects_this_scan"],
                            "positions": len(state["positions"]),
                            "hhmm": hhmm}, "%s %s" % (date_str, hhmm))
        return results
    finally:
        _SCANNING = False


# ---------------------------------------------------------------- QMT 回调入口
def on_timer(C):
    """QMT run_time 定时器回调（实盘用真实时钟）。"""
    date_str, hhmm, ts = broker_api.now_parts()
    state = getattr(C, "claw_state", None)
    if state is None:
        state = init(C)
    return scan_once(C, state, date_str, hhmm, ts)


def handlebar(C):
    """QMT 每根 bar 回调（回测/历史模式用 C.date / C.hhmm）。"""
    state = getattr(C, "claw_state", None)
    if state is None:
        state = init(C)
    date_str = str(getattr(C, "date", "") or "")
    hhmm = str(getattr(C, "hhmm", "10:30") or "10:30")
    return scan_once(C, state, date_str, hhmm, broker_api.now_parts()[2])


# ---------------------------------------------------------------- 对账 / 开关 / 状态
def check_reconciliation(C, broker_positions, broker_cash):
    """盘前 / 盘后以券商数据为准对账；有差异立即熔断。"""
    state = getattr(C, "claw_state", None)
    if state is None:
        state = init(C)
    diffs = reconcile.reconcile(broker_positions, state["ledger_positions"],
                                broker_cash, state["ledger_cash"])
    if reconcile.should_halt(diffs):
        state["halt"] = True
        state["audit"].append("reconcile_halt", {"diffs": diffs}, "")
    else:
        state["audit"].append("reconcile_ok", {"positions": len(state["ledger_positions"])}, "")
    return diffs


def reconcile_now(C):
    """主动向券商查询并与影子账本对账。"""
    state = getattr(C, "claw_state", None)
    if state is None:
        state = init(C)
    positions = broker_api.query_positions(C, state["account_id"])
    account = broker_api.query_account(C, state["account_id"])
    return check_reconciliation(C, dict((k, v["qty"]) for k, v in positions.items()),
                                float(account.get("cash", 0) or 0))


def set_kill_switch(C, value, reason=""):
    """运行时一键停用 / 恢复。停用会立刻停止后续委托。"""
    global KILL_SWITCH
    KILL_SWITCH = bool(value)
    state = getattr(C, "claw_state", None)
    if state is None:
        state = init(C)
    state["orders"].kill_switch = KILL_SWITCH
    state["audit"].append("kill_switch", {"value": KILL_SWITCH, "reason": reason}, "")
    return KILL_SWITCH


def status(C):
    """给 QMT 界面/日志用的实时状态快照（账号脱敏，无任何凭据）。"""
    state = getattr(C, "claw_state", None)
    if state is None:
        state = init(C)
    items = state["audit"].dump()
    return {
        "broker": cfg.BROKER_NAME,
        "config": cfg.snapshot(),
        "effective_dry_run": state["orders"].dry_run,
        "kill_switch": KILL_SWITCH,
        "halt": state["halt"],
        "account_id": audit.mask(state["account_id"]) if state["account_id"] else "(未探测到)",
        "cash": float((state["account"] or {}).get("cash", 0) or 0),
        "positions": dict((k, {"qty": v["qty"], "available": v["available"]})
                          for k, v in state["positions"].items()),
        "day_orders": state["day_orders"],
        "day_turnover": round(state["day_turnover"], 2),
        "orders_total": len(state["orders"]),
        "orders_unconfirmed": len(state["orders"].unconfirmed()),
        "rule_digest": RULE_DIGEST,
        "timer_started": state["timer_started"],
        "probe": state["probe"],
        "recent_signals": [i for i in items if i["kind"] == "signal"][-10:],
        "recent_rejects": [i for i in items if i["kind"] == "risk_reject"][-10:],
    }
