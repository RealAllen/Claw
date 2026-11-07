# -*- coding: utf-8 -*-
"""共享策略内核：纯函数、零 IO、零第三方依赖、Python 3.6 语法兼容。

这份文件是回测与券商沙箱执行层共用的唯一逻辑来源。
禁止在此文件里加入文件读写、网络请求、时间读取或任何平台 API 调用：
所有外部输入都由调用方以参数传入。
"""
from __future__ import unicode_literals

import hashlib

SUPPORTED_OPS = (">", ">=", "<", "<=", "==", "!=", "between", "in", "not_in")
SUPPORTED_LOGIC = ("AND", "OR")


def compare(value, op, target):
    """单次比较。字段缺失由调用方处理，这里只做数值/布尔比较。"""
    if op not in SUPPORTED_OPS:
        raise ValueError("unsupported operator: %s" % op)
    if op == ">":
        return value > target
    if op == ">=":
        return value >= target
    if op == "<":
        return value < target
    if op == "<=":
        return value <= target
    if op == "==":
        return value == target
    if op == "!=":
        return value != target
    if op == "between":
        if not isinstance(target, (list, tuple)) or len(target) != 2:
            raise ValueError("between needs [low, high]")
        return target[0] <= value <= target[1]
    if op == "in":
        return value in target
    if op == "not_in":
        return value not in target
    raise ValueError("unsupported operator: %s" % op)


def evaluate_condition(metrics, condition):
    """返回 (bool, reason)。字段缺失一律判不通过，不用 truthy 兜底。"""
    field = condition.get("field")
    op = condition.get("op")
    if not field or not op:
        return False, "invalid_condition:missing_field_or_op"
    if op not in SUPPORTED_OPS:
        return False, "invalid_condition:op=%s" % op
    if field not in metrics or metrics[field] is None:
        return False, "%s:missing" % field
    value = metrics[field]
    target = condition.get("value")
    try:
        ok = compare(value, op, target)
    except (TypeError, ValueError) as exc:
        return False, "%s:compare_error(%s)" % (field, exc)
    reason = "%s=%s %s %s -> %s" % (field, _short(value), op, _short(target), "PASS" if ok else "FAIL")
    return ok, reason


def evaluate_group(metrics, conditions, logic):
    """返回 (bool, reasons)。空条件组视为通过。"""
    if logic not in SUPPORTED_LOGIC:
        raise ValueError("unsupported logic: %s" % logic)
    if not conditions:
        return True, []
    results = []
    for cond in conditions:
        results.append(evaluate_condition(metrics, cond))
    flags = [r[0] for r in results]
    reasons = [r[1] for r in results]
    ok = all(flags) if logic == "AND" else any(flags)
    return ok, reasons


def check_universe(snapshot, universe):
    """标的池过滤，返回 (bool, reasons)。"""
    reasons = []
    if universe.get("exclude_st") and snapshot.get("is_st"):
        reasons.append("universe:ST")
    if universe.get("exclude_suspended") and snapshot.get("suspended"):
        reasons.append("universe:suspended")
    if universe.get("exclude_limit_up") and snapshot.get("high_limit"):
        reasons.append("universe:limit_up")
    if universe.get("exclude_limit_down") and snapshot.get("low_limit"):
        reasons.append("universe:limit_down")
    boards = universe.get("boards")
    if boards:
        if snapshot.get("board") not in boards:
            reasons.append("universe:board=%s" % snapshot.get("board"))
    min_days = universe.get("min_list_days")
    if min_days is not None:
        days = snapshot.get("list_days")
        if days is None or days < min_days:
            reasons.append("universe:list_days=%s<%s" % (days, min_days))
    min_amount = universe.get("min_amount")
    if min_amount is not None:
        amount = snapshot.get("amount")
        if amount is None or amount < min_amount:
            reasons.append("universe:amount=%s<%s" % (amount, min_amount))
    return (len(reasons) == 0), reasons


def to_minutes(hhmm):
    """'09:35' -> 575。格式错误返回 None。"""
    if not isinstance(hhmm, str) or len(hhmm) != 5 or hhmm[2] != ":":
        return None
    try:
        return int(hhmm[0:2]) * 60 + int(hhmm[3:5])
    except ValueError:
        return None


def in_time_window(hhmm, windows):
    """返回 (bool, reason)。windows 为空表示不限制。"""
    if not windows:
        return True, "time_window:unrestricted"
    now = to_minutes(hhmm)
    if now is None:
        return False, "time_window:invalid_time=%s" % hhmm
    for pair in windows:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        start = to_minutes(pair[0])
        end = to_minutes(pair[1])
        if start is None or end is None:
            continue
        if start <= now <= end:
            return True, "time_window:%s in %s-%s" % (hhmm, pair[0], pair[1])
    return False, "time_window:%s outside" % hhmm


def make_signal(symbol, side, as_of, rules, metrics, reasons):
    """生成带幂等 ID 的信号。client_order_id 由内容确定性派生，可跨层核对。"""
    version = rules.get("version", "0")
    raw = "%s|%s|%s|%s|%s" % (symbol, side, as_of, version, rules.get("name", ""))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    return {
        "symbol": symbol,
        "side": side,
        "as_of": as_of,
        "rule_version": version,
        "rule_name": rules.get("name", ""),
        "mode": rules.get("mode", "semi_auto"),
        "client_order_id": digest,
        "reasons": list(reasons),
        "metrics": dict(metrics),
        "price": metrics.get("last"),
    }


def evaluate_level1(snapshot, rules):
    """只评估一级（快照类）条件，返回通过的 side 列表。

    调用方据此决定是否值得为候选标的计算 K 线类指标，实现两级筛选的性能约束。
    """
    sides = []
    for side in ("buy", "sell"):
        group = rules.get(side)
        if not group:
            continue
        ok, _ = evaluate_group(snapshot, group.get("conditions", []), group.get("logic", "AND"))
        if ok:
            sides.append(side)
    return sides


def evaluate(symbol, snapshot, precision, rules):
    """主入口：返回信号 dict 或 None，以及完整判定理由。

    snapshot: 一级快照指标 dict（含 last / pct_chg / hhmm / 标的池字段等）
    precision: 二级 K 线指标 dict（可为 None 或 {}）
    rules: 规则 dict
    """
    all_reasons = []
    ok_universe, uni_reasons = check_universe(snapshot, rules.get("universe", {}))
    all_reasons.extend(uni_reasons)
    if not ok_universe:
        return None, all_reasons

    ok_time, time_reason = in_time_window(snapshot.get("hhmm"), rules.get("time_windows"))
    all_reasons.append(time_reason)
    if not ok_time:
        return None, all_reasons

    merged = dict(snapshot)
    if precision:
        merged.update(precision)

    for side in ("buy", "sell"):
        group = rules.get(side)
        if not group:
            continue
        ok1, r1 = evaluate_group(merged, group.get("conditions", []), group.get("logic", "AND"))
        all_reasons.extend(["%s.%s" % (side, x) for x in r1])
        if not ok1:
            continue
        ok2, r2 = evaluate_group(merged, group.get("precision_conditions", []),
                                 group.get("precision_logic", "AND"))
        all_reasons.extend(["%s.precision.%s" % (side, x) for x in r2])
        if not ok2:
            continue
        signal = make_signal(symbol, side, snapshot.get("as_of", ""), rules, merged, all_reasons)
        return signal, all_reasons
    return None, all_reasons


def check_risk(signal, account, rules, hhmm):
    """下单前硬前置校验（纯函数）。返回 (bool, reasons)。

    account 至少含：cash, total_equity, positions{symbol: qty},
    day_orders, day_turnover, day_loss_pct, seen_ids(list),
    last_signal_minutes(dict), kill_switch(bool)。
    """
    reasons = []
    risk = rules.get("risk", {})
    if account.get("kill_switch"):
        reasons.append("risk:kill_switch_on")
    if signal.get("client_order_id") in account.get("seen_ids", []):
        reasons.append("risk:duplicate_signal")

    cooldown = rules.get("cooldown_minutes", 0)
    if cooldown:
        now = to_minutes(hhmm)
        last = account.get("last_signal_minutes", {}).get(signal.get("symbol"))
        if now is not None and last is not None and now - last < cooldown:
            reasons.append("risk:cooldown(%s min left)" % (cooldown - (now - last)))

    price = signal.get("price") or 0
    qty = signal.get("qty") or 0
    side = signal.get("side")
    order_amount = price * qty

    lot = risk.get("lot_size", 100)
    if lot and qty % lot != 0:
        reasons.append("risk:lot_size(qty=%s,lot=%s)" % (qty, lot))
    if qty <= 0:
        reasons.append("risk:qty<=0")
    max_amount = risk.get("max_order_amount")
    if max_amount is not None and order_amount > max_amount:
        reasons.append("risk:order_amount=%s>%s" % (order_amount, max_amount))
    max_orders = risk.get("max_daily_orders")
    if max_orders is not None and account.get("day_orders", 0) >= max_orders:
        reasons.append("risk:day_orders=%s>=%s" % (account.get("day_orders"), max_orders))
    max_loss = risk.get("max_daily_loss_pct")
    if max_loss is not None and account.get("day_loss_pct", 0) >= max_loss:
        reasons.append("risk:day_loss=%s>=%s" % (account.get("day_loss_pct"), max_loss))
    max_turnover = risk.get("max_daily_turnover")
    if max_turnover is not None and account.get("day_turnover", 0) + order_amount > max_turnover:
        reasons.append("risk:day_turnover")

    equity = account.get("total_equity") or 0
    if equity > 0:
        max_pos = risk.get("max_position_pct")
        if max_pos is not None:
            held = account.get("positions", {}).get(signal.get("symbol"), 0)
            value_after = (held + qty if side == "buy" else 0) * price
            if (value_after / equity) * 100.0 > max_pos:
                reasons.append("risk:single_position>%s%%" % max_pos)
        max_total = risk.get("max_total_position_pct")
        if max_total is not None and side == "buy":
            prices = account.get("position_prices", {})
            held_value = 0.0
            for sym, held_qty in account.get("positions", {}).items():
                held_value += held_qty * prices.get(sym, price)
            if ((held_value + order_amount) / equity) * 100.0 > max_total:
                reasons.append("risk:total_position>%s%%" % max_total)

    if side == "buy":
        if order_amount > account.get("cash", 0):
            reasons.append("risk:insufficient_cash")
    elif side == "sell":
        held = account.get("positions", {}).get(signal.get("symbol"), 0)
        locked = account.get("t1_locked", {}).get(signal.get("symbol"), 0)
        available = max(0, held - locked)
        if qty > available:
            reasons.append("risk:insufficient_position_or_T+1_locked(available=%s,qty=%s)" % (available, qty))

    return (len(reasons) == 0), reasons


# ---------------------------------------------------------------- 买入扫描 / 持仓卖出
def evaluate_level1_entry(snapshot, rules):
    """全市场粗筛：只评估买入的一级（快照类）条件，不做 K 线计算。"""
    group = rules.get("buy")
    if not group:
        return False, []
    return evaluate_group(snapshot, group.get("conditions", []), group.get("logic", "AND"))


def evaluate_entry(symbol, snapshot, precision, rules):
    """全市场扫描只产生买入信号；卖出一律由 evaluate_exit 针对持仓单独处理。"""
    scan_rules = dict(rules)
    scan_rules.pop("sell", None)
    scan_rules.pop("exit", None)
    return evaluate(symbol, snapshot, precision, scan_rules)


def position_metrics(position, snapshot, hhmm):
    """把持仓状态换算成可写进规则 DSL 的卖出指标（纯函数）。

    position 至少含 qty / available / cost / entry_hhmm / high_since_entry。
    字段缺失给 None，由 evaluate_condition 判为不通过，不做 truthy 兜底。
    """
    last = snapshot.get("last")
    cost = position.get("cost")
    high = position.get("high_since_entry")
    qty = position.get("qty", 0) or 0
    metrics = {
        "held_qty": qty,
        "available_qty": position.get("available", 0),
        "cost_price": cost,
        "entry_hhmm": position.get("entry_hhmm"),
    }
    metrics["pnl_pct"] = (round((last / float(cost) - 1.0) * 100.0, 4)
                          if (last is not None and cost) else None)
    metrics["pnl_amount"] = (round((last - cost) * qty, 2)
                             if (last is not None and cost is not None) else None)
    metrics["drawdown_from_high_pct"] = (round((last / float(high) - 1.0) * 100.0, 4)
                                         if (last and high) else None)
    start = to_minutes(position.get("entry_hhmm") or "")
    now = to_minutes(hhmm)
    metrics["hold_minutes"] = ((now - start)
                               if (start is not None and now is not None and now >= start)
                               else None)
    return metrics


def _exit_group(rules):
    return rules.get("sell") or rules.get("exit") or {}


def evaluate_exit_level1(snapshot, position, rules, hhmm):
    """持仓卖出的一级判定，不需要历史 K 线。"""
    merged = dict(snapshot)
    merged.update(position_metrics(position, snapshot, hhmm))
    group = _exit_group(rules)
    return evaluate_group(merged, group.get("conditions", []), group.get("logic", "AND"))


def evaluate_exit(symbol, snapshot, precision, position, rules, hhmm):
    """只对已持有仓位产生卖出信号。返回 (signal_or_None, reasons)。"""
    reasons = []
    ok_time, reason = in_time_window(hhmm, rules.get("time_windows"))
    reasons.append(reason)
    if not ok_time:
        return None, reasons
    merged = dict(snapshot)
    if precision:
        merged.update(precision)
    merged.update(position_metrics(position, snapshot, hhmm))
    group = _exit_group(rules)
    ok1, r1 = evaluate_group(merged, group.get("conditions", []), group.get("logic", "AND"))
    reasons.extend(["sell.%s" % x for x in r1])
    if not ok1:
        return None, reasons
    ok2, r2 = evaluate_group(merged, group.get("precision_conditions", []),
                             group.get("precision_logic", "AND"))
    reasons.extend(["sell.precision.%s" % x for x in r2])
    if not ok2:
        return None, reasons
    return make_signal(symbol, "sell", snapshot.get("as_of", ""), rules, merged, reasons), reasons


def _short(value):
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."
