# -*- coding: utf-8 -*-
"""回测 / 纸上交易引擎：T+1、费用、滑点、风控前置、信号流水与审计记录。

纯本地模拟，不连接任何券商账户，不发出任何真实委托。
"""
from __future__ import division

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared import strategy_core as core  # noqa: E402
from research import indicators, metrics, rules_engine  # noqa: E402

DEFAULT_SCENARIOS = [
    ("单日 -3% 情景", -3.0),
    ("单日 -5% 情景", -5.0),
    ("2015 式连续回撤近似 -15%", -15.0),
    ("流动性干涸近似 -20%", -20.0),
]


def _timeline(grouped):
    points = set()
    for bars in grouped.values():
        for bar in bars:
            points.add((bar["date"], bar["hhmm"]))
    return sorted(points)


def _by_time(grouped):
    index = {}
    for symbol, bars in grouped.items():
        for pos, bar in enumerate(bars):
            index.setdefault((bar["date"], bar["hhmm"]), []).append((symbol, bar, pos))
    return index


def _order_qty(side, price, account, rules):
    risk = rules.get("risk", {})
    lot = int(risk.get("lot_size", 100)) or 100
    max_amount = float(risk.get("max_order_amount", 20000))
    equity = account.get("total_equity", 0.0)
    max_pos_pct = float(risk.get("max_position_pct", 10))
    if price is None or price <= 0:
        return 0
    if side == "buy":
        budget = min(max_amount, equity * max_pos_pct / 100.0, account.get("cash", 0) * 0.98)
    else:
        held = account.get("positions", {}).get(account.get("_symbol"), 0)
        locked = account.get("t1_locked", {}).get(account.get("_symbol"), 0)
        available = max(0, held - locked)
        budget = min(max_amount, available * price, equity * max_pos_pct / 100.0)
    qty = int(budget / (price * lot)) * lot
    return max(0, qty)


def run_backtest(grouped, rules, initial_cash=1000000.0, fee_rate=0.0003,
                 stamp_duty=0.001, slippage=0.001, scenarios=None):
    """返回包含绩效、信号流水、拒单审计与净值曲线的 dict。"""
    timeline = _timeline(grouped)
    by_time = _by_time(grouped)
    account = {
        "cash": initial_cash,
        "total_equity": initial_cash,
        "positions": {},
        "position_prices": {},
        "cost_basis": {},
        "entry_hhmm": {},
        "high_since_entry": {},
        "day_orders": 0,
        "day_turnover": 0.0,
        "day_loss_pct": 0.0,
        "seen_ids": [],
        "last_signal_minutes": {},
        "t1_locked": {},
        "kill_switch": False,
    }
    last_prices = {}
    events, rejections, equity_curve, trade_pnls = [], [], [], []
    current_date, day_start_equity = None, initial_cash

    for date, hhmm in timeline:
        if date != current_date:
            current_date = date
            day_start_equity = account["cash"] + sum(
                qty * last_prices.get(sym, 0.0) for sym, qty in account["positions"].items())
            account["day_orders"] = 0
            account["day_turnover"] = 0.0
            account["day_loss_pct"] = 0.0
            account["last_signal_minutes"] = {}
            account["t1_locked"] = {}

        for symbol, bar, pos in by_time.get((date, hhmm), []):
            last_prices[symbol] = bar["close"]
            account["position_prices"][symbol] = bar["close"]
            held = account["positions"].get(symbol, 0)
            if held > 0:
                peak = bar.get("high") if bar.get("high") is not None else bar["close"]
                account["high_since_entry"][symbol] = max(
                    account["high_since_entry"].get(symbol, bar["close"]), peak)
            history = grouped[symbol][:pos]
            snapshot = indicators.snapshot_metrics(bar, history)

            position = None
            if held > 0:
                position = {
                    "qty": held,
                    "available": max(0, held - account["t1_locked"].get(symbol, 0)),
                    "cost": account["cost_basis"].get(symbol),
                    "entry_hhmm": account["entry_hhmm"].get(symbol),
                    "high_since_entry": account["high_since_entry"].get(symbol),
                }

            def provider(sym, _symbol=symbol, _pos=pos):
                return indicators.precision_metrics(grouped[_symbol][:_pos + 1])

            signal, reasons = rules_engine.scan_symbol(symbol, snapshot, provider,
                                                       rules, position)
            if signal is None:
                if any("level1:matched" in r for r in reasons):
                    rejections.append({
                        "as_of": snapshot.get("as_of"), "symbol": symbol,
                        "stage": "precision", "reasons": reasons[-6:],
                    })
                continue

            price = signal.get("price")
            account["_symbol"] = symbol
            qty = _order_qty(signal["side"], price, account, rules)
            signal["qty"] = qty
            equity = account["cash"] + sum(
                q * last_prices.get(s, 0.0) for s, q in account["positions"].items())
            account["total_equity"] = equity
            if day_start_equity > 0:
                account["day_loss_pct"] = max(0.0, (day_start_equity - equity) / day_start_equity * 100.0)

            ok, risk_reasons = core.check_risk(signal, account, rules, hhmm)
            if not ok:
                rejections.append({
                    "as_of": snapshot.get("as_of"), "symbol": symbol,
                    "side": signal["side"], "stage": "risk",
                    "client_order_id": signal["client_order_id"],
                    "reasons": risk_reasons,
                })
                continue

            gross = price * qty
            if signal["side"] == "buy":
                cost = gross * (1.0 + slippage) + gross * fee_rate
                account["cash"] -= cost
                old_qty = account["positions"].get(symbol, 0)
                old_cost = account["cost_basis"].get(symbol, price)
                account["cost_basis"][symbol] = (old_cost * old_qty + price * qty) / (old_qty + qty)
                account["positions"][symbol] = old_qty + qty
                account["t1_locked"][symbol] = account["t1_locked"].get(symbol, 0) + qty
                if old_qty == 0:
                    account["entry_hhmm"][symbol] = hhmm
                    account["high_since_entry"][symbol] = bar.get("high") or price
            else:
                proceeds = gross * (1.0 - slippage) - gross * (fee_rate + stamp_duty)
                account["cash"] += proceeds
                avg_cost = account["cost_basis"].get(symbol, price)
                trade_pnls.append((price - avg_cost) * qty - gross * (fee_rate + stamp_duty + 2 * slippage))
                account["positions"][symbol] = account["positions"].get(symbol, 0) - qty
                if account["positions"][symbol] <= 0:
                    account["positions"].pop(symbol, None)
                    account["cost_basis"].pop(symbol, None)
                    account["entry_hhmm"].pop(symbol, None)
                    account["high_since_entry"].pop(symbol, None)
                    account["t1_locked"].pop(symbol, None)

            account["day_orders"] += 1
            account["day_turnover"] += gross
            account["seen_ids"].append(signal["client_order_id"])
            minutes = core.to_minutes(hhmm)
            if minutes is not None:
                account["last_signal_minutes"][symbol] = minutes
            events.append({
                "as_of": snapshot.get("as_of"), "symbol": symbol,
                "side": signal["side"], "price": round(price, 3), "qty": qty,
                "amount": round(gross, 2),
                "client_order_id": signal["client_order_id"],
                "rule_version": signal["rule_version"],
                "mode": signal["mode"],
                "cash_after": round(account["cash"], 2),
                "reasons": reasons[-4:],
            })

        equity = account["cash"] + sum(q * last_prices.get(s, 0.0) for s, q in account["positions"].items())
        equity_curve.append(equity)

    summary = metrics.summarize(equity_curve, trade_pnls, initial_cash)
    return {
        "kind": "backtest",
        "is_demo": any(b.get("source") == "DEMO" for bars in grouped.values() for b in bars[:1]),
        "rules": {"name": rules.get("name"), "version": rules.get("version"),
                  "digest": rules_engine.rule_digest(rules)},
        "assumptions": {
            "initial_cash": initial_cash, "fee_rate": fee_rate,
            "stamp_duty": stamp_duty, "slippage": slippage,
            "t_plus_1": True, "note": "模拟盘，非真实成交",
        },
        "summary": summary,
        "equity_curve": [round(v, 2) for v in equity_curve],
        "events": events,
        "rejections": rejections,
        "stress": metrics.stress_test(equity_curve, scenarios or DEFAULT_SCENARIOS),
        "final_positions": dict(account["positions"]),
    }


def scan_latest(grouped, rules):
    """对每只标的的最新一根 bar 做一次扫描（两级筛选 + 风控）。"""
    results = []
    for symbol, bars in sorted(grouped.items()):
        if not bars:
            continue
        pos = len(bars) - 1
        bar = bars[pos]
        snapshot = indicators.snapshot_metrics(bar, bars[:pos])

        def provider(sym, _bars=bars, _pos=pos):
            return indicators.precision_metrics(_bars[:_pos + 1])

        signal, reasons = rules_engine.scan_symbol(symbol, snapshot, provider, rules)
        results.append({
            "symbol": symbol,
            "as_of": snapshot.get("as_of"),
            "last": snapshot.get("last"),
            "pct_chg": snapshot.get("pct_chg"),
            "vol_x": snapshot.get("vol_x"),
            "signal": None if signal is None else {
                "side": signal["side"],
                "client_order_id": signal["client_order_id"],
                "mode": signal["mode"],
            },
            "reasons": reasons[-5:],
        })
    return {
        "kind": "scan",
        "is_demo": any(bars and bars[-1].get("source") == "DEMO" for bars in grouped.values()),
        "rules": {"name": rules.get("name"), "version": rules.get("version"),
                  "digest": rules_engine.rule_digest(rules)},
        "count": len(results),
        "signals": [r for r in results if r["signal"]],
        "rows": results,
    }
