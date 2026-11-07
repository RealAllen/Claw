# -*- coding: utf-8 -*-
"""规则加载、校验与两级筛选编排。

策略判定逻辑全部委托 shared.strategy_core，本模块只负责 IO 与编排。
"""
import hashlib
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared import strategy_core as core  # noqa: E402

# 唯一生效的生产规则文件：本地看板的配置台与 QMT 沙箱导出都读写它。
# shared/rules/example_momentum.json 仅保留作历史示例，不再参与默认流程。
DEFAULT_RULES = os.path.join(_ROOT, "shared", "rules", "live.json")


def load_rules(path=None):
    """读取并校验规则 JSON。"""
    path = path or DEFAULT_RULES
    with open(path, "r", encoding="utf-8") as handle:
        rules = json.load(handle)
    validate_rules(rules)
    rules["_source_path"] = os.path.abspath(path)
    return rules


def validate_rules(rules):
    """规则结构校验：缺字段/非法操作符/非法时间窗一律提前报错，不静默兜底。"""
    if not isinstance(rules, dict):
        raise ValueError("rules must be a dict")
    for key in ("version", "name", "buy", "sell"):
        if key not in rules:
            raise ValueError("rules missing required key: %s" % key)
    for side in ("buy", "sell"):
        group = rules[side]
        if not isinstance(group, dict):
            raise ValueError("%s must be a dict" % side)
        logic = group.get("logic", "AND")
        if logic not in core.SUPPORTED_LOGIC:
            raise ValueError("%s.logic invalid: %s" % (side, logic))
        for key in ("conditions", "precision_conditions"):
            for cond in group.get(key, []):
                if "field" not in cond or "op" not in cond or "value" not in cond:
                    raise ValueError("%s.%s has incomplete condition: %s" % (side, key, cond))
                if cond["op"] not in core.SUPPORTED_OPS:
                    raise ValueError("%s.%s invalid op: %s" % (side, key, cond["op"]))
                if cond["op"] == "between":
                    val = cond["value"]
                    if not isinstance(val, (list, tuple)) or len(val) != 2:
                        raise ValueError("between needs [low, high]: %s" % cond)
    for pair in rules.get("time_windows", []):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("time_window must be [start, end]: %s" % pair)
        for hhmm in pair:
            if core.to_minutes(hhmm) is None:
                raise ValueError("invalid HH:MM: %s" % hhmm)
    risk = rules.get("risk", {})
    for key in ("max_order_amount", "max_daily_orders", "lot_size"):
        if key in risk and not isinstance(risk[key], (int, float)):
            raise ValueError("risk.%s must be numeric" % key)
    return True


def rule_digest(rules):
    """规则内容的确定性指纹，用于核对回测与沙箱副本是否一致。"""
    payload = dict(rules)
    payload.pop("_source_path", None)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def scan_symbol(symbol, snapshot, precision_provider, rules, position=None):
    """两级筛选编排：先标的池/时间窗，再一级快照条件，最后才按需算 K 线指标。

    position 非空（持仓）时走「持仓卖出」判定，否则走「全市场买入」判定 —— 与
    QMT 实盘执行层共用 shared.strategy_core 的同一套函数，避免回测一套、实盘一套。

    precision_provider(symbol) -> dict，只在有一级信号时才被调用（惰性计算）。
    返回 (signal_or_None, reasons)。
    """
    all_reasons = []
    ok_universe, reasons = core.check_universe(snapshot, rules.get("universe", {}))
    all_reasons.extend(reasons)
    if not ok_universe:
        return None, all_reasons

    hhmm = snapshot.get("hhmm")
    ok_time, reason = core.in_time_window(hhmm, rules.get("time_windows"))
    all_reasons.append(reason)
    if not ok_time:
        return None, all_reasons

    if position and position.get("qty", 0) > 0:
        ok1, r1 = core.evaluate_exit_level1(snapshot, position, rules, hhmm)
        all_reasons.extend(r1)
        if not ok1:
            all_reasons.append("level1:no_exit_match")
            return None, all_reasons
        all_reasons.append("level1:exit_matched")
        precision = precision_provider(symbol) if precision_provider else {}
        signal, eval_reasons = core.evaluate_exit(symbol, snapshot, precision,
                                                 position, rules, hhmm)
        all_reasons.extend(eval_reasons)
        return signal, all_reasons

    ok1, r1 = core.evaluate_level1_entry(snapshot, rules)
    all_reasons.extend(r1)
    if not ok1:
        all_reasons.append("level1:no_entry_match")
        return None, all_reasons
    all_reasons.append("level1:entry_matched")

    precision = precision_provider(symbol) if precision_provider else {}
    signal, eval_reasons = core.evaluate_entry(symbol, snapshot, precision, rules)
    all_reasons.extend(eval_reasons)
    return signal, all_reasons
