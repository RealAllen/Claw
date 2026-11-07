# -*- coding: utf-8 -*-
"""绩效与风险度量：纯标准库。"""
from __future__ import division

import math
import statistics


def pct_returns(equity_curve):
    result = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev > 0:
            result.append((equity_curve[i] - prev) / prev)
    return result


def max_drawdown(equity_curve):
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst * 100.0


def sharpe(returns, periods_per_year=252.0, risk_free=0.0):
    if len(returns) < 2:
        return None
    sd = statistics.pstdev(returns)
    if sd == 0:
        return None
    return (statistics.mean(returns) - risk_free / periods_per_year) / sd * math.sqrt(periods_per_year)


def var_cvar(returns, confidence=0.95):
    """历史模拟法 VaR / CVaR，返回正数百分比损失。"""
    if not returns:
        return None, None
    ordered = sorted(returns)
    cutoff = max(1, int(math.floor((1.0 - confidence) * len(ordered))))
    tail = ordered[:cutoff]
    var = -ordered[cutoff - 1] * 100.0
    cvar = -statistics.mean(tail) * 100.0 if tail else None
    return var, cvar


def summarize(equity_curve, trade_pnls, initial_cash, periods_per_year=252.0):
    """返回绩效摘要 dict。"""
    if not equity_curve:
        return {"error": "empty equity curve"}
    final = equity_curve[-1]
    returns = pct_returns(equity_curve)
    total_return = (final / initial_cash - 1.0) * 100.0 if initial_cash else 0.0
    years = len(equity_curve) / periods_per_year if periods_per_year else 0.0
    annualized = ((final / initial_cash) ** (1.0 / years) - 1.0) * 100.0 if years > 0 and final > 0 else None
    wins = len([p for p in trade_pnls if p > 0])
    var95, cvar95 = var_cvar(returns, 0.95)
    mdd = max_drawdown(equity_curve)
    return {
        "initial_cash": round(initial_cash, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round(total_return, 4),
        "annualized_return_pct": round(annualized, 4) if annualized is not None else None,
        "max_drawdown_pct": round(mdd, 4),
        "sharpe": round(sharpe(returns, periods_per_year), 4) if sharpe(returns, periods_per_year) is not None else None,
        "var95_pct": round(var95, 4) if var95 is not None else None,
        "cvar95_pct": round(cvar95, 4) if cvar95 is not None else None,
        "closed_trades": len(trade_pnls),
        "win_rate_pct": round(wins * 100.0 / len(trade_pnls), 4) if trade_pnls else None,
        "observations": len(equity_curve),
        "note": "模拟盘结果，基于演示数据时不可作为投资依据",
    }


def stress_test(equity_curve, scenarios):
    """简单情景压力测试：按给定单期冲击作用于当前净值。

    仅用于风险画像，不是对未来损失的预测。
    """
    if not equity_curve:
        return []
    current = equity_curve[-1]
    result = []
    for name, shock_pct in scenarios:
        result.append({
            "scenario": name,
            "shock_pct": shock_pct,
            "equity_after": round(current * (1.0 + shock_pct / 100.0), 2),
            "loss": round(current * shock_pct / 100.0, 2),
        })
    return result
