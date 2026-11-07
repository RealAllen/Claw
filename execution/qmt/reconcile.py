# -*- coding: utf-8 -*-
"""对账：券商实际持仓/资金 vs 本地信号账本，纯内存纯函数。"""
from __future__ import division


def reconcile(broker_positions, ledger_positions, broker_cash, ledger_cash, tolerance=0.01):
    """返回差异列表；空列表表示对账一致。

    broker_positions / ledger_positions: {symbol: qty}
    """
    diffs = []
    symbols = set(broker_positions or {}) | set(ledger_positions or {})
    for symbol in sorted(symbols):
        b = float((broker_positions or {}).get(symbol, 0))
        l = float((ledger_positions or {}).get(symbol, 0))
        if abs(b - l) > tolerance:
            diffs.append({"field": "position", "symbol": symbol, "broker": b, "ledger": l, "delta": b - l})
    if abs(float(broker_cash or 0) - float(ledger_cash or 0)) > tolerance:
        diffs.append({
            "field": "cash",
            "broker": float(broker_cash or 0),
            "ledger": float(ledger_cash or 0),
            "delta": float(broker_cash or 0) - float(ledger_cash or 0),
        })
    return diffs


def should_halt(diffs):
    """有差异即熔断。"""
    return len(diffs) > 0
