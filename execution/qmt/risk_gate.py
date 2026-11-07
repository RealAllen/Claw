# -*- coding: utf-8 -*-
"""下单前硬前置风控闸门（纯函数，无 IO）。"""
from __future__ import division

try:
    from . import strategy_core as core
except (ImportError, ValueError):
    import strategy_core as core


def size_order(side, price, symbol, account, rules):
    """按单笔上限 / 单票仓位上限 / 可用资金或持仓反推数量，向下取整到最小交易单位。"""
    risk = rules.get("risk", {})
    lot = int(risk.get("lot_size", 100)) or 100
    max_amount = float(risk.get("max_order_amount", 20000))
    max_pos_pct = float(risk.get("max_position_pct", 10))
    equity = float(account.get("total_equity", 0.0))
    if price is None or price <= 0 or equity <= 0:
        return 0
    if side == "buy":
        budget = min(max_amount, equity * max_pos_pct / 100.0, float(account.get("cash", 0)) * 0.98)
    else:
        symbol_key = symbol
        held = float(account.get("positions", {}).get(symbol_key, 0))
        locked = float(account.get("t1_locked", {}).get(symbol_key, 0))
        available = max(0.0, held - locked)
        budget = min(max_amount, available * price, equity * max_pos_pct / 100.0)
    return max(0, int(budget / (price * lot)) * lot)


def validate_order(signal):
    """订单参数结构校验，返回 (bool, reasons)。"""
    reasons = []
    if signal.get("side") not in ("buy", "sell"):
        reasons.append("order:invalid_side")
    if not signal.get("symbol"):
        reasons.append("order:missing_symbol")
    if not signal.get("client_order_id"):
        reasons.append("order:missing_idempotency_id")
    price = signal.get("price")
    qty = signal.get("qty")
    if price is None or price <= 0:
        reasons.append("order:invalid_price")
    if qty is None or qty <= 0:
        reasons.append("order:invalid_qty")
    return (len(reasons) == 0), reasons


def precheck(signal, account, rules, hhmm):
    """依次做参数校验 + 共享风控硬校验。任一不通过即拒绝。"""
    ok_shape, shape_reasons = validate_order(signal)
    if not ok_shape:
        return False, shape_reasons
    ok_risk, risk_reasons = core.check_risk(signal, account, rules, hhmm)
    return ok_risk, shape_reasons + risk_reasons
