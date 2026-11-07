# -*- coding: utf-8 -*-
"""指标计算：纯 Python 标准库，无第三方依赖。

一级快照类：可批量算，适合全市场粗筛。
二级 K 线类：需逐只取历史序列，只对候选池计算。
"""
from __future__ import division

import statistics

BOARD_LIMIT = {"主板": 10.0, "创业板": 20.0, "科创板": 20.0, "北交所": 30.0}


def _pct_change(now, base):
    if base is None or now is None or base == 0:
        return None
    return (now / base - 1.0) * 100.0


def _mean(values):
    clean = [v for v in values if v is not None]
    return statistics.mean(clean) if clean else None


def sma(values, n):
    if len(values) < n or n <= 0:
        return None
    return statistics.mean(values[-n:])


def ema_series(values, n):
    """指数移动平均序列，返回与输入等长的 list。"""
    if not values or n <= 0:
        return []
    alpha = 2.0 / (n + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def macd(closes, fast=12, slow=26, signal=9):
    """返回 (dif, dea, hist)；数据不足返回 (None, None, None)。"""
    if len(closes) < slow + signal:
        return None, None, None
    fast_line = ema_series(closes, fast)
    slow_line = ema_series(closes, slow)
    dif = [a - b for a, b in zip(fast_line, slow_line)]
    dea = ema_series(dif, signal)
    return dif[-1], dea[-1], 2.0 * (dif[-1] - dea[-1])


def rsi(closes, n=6):
    """Wilder 平滑 RSI。"""
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = statistics.mean(gains[:n])
    avg_loss = statistics.mean(losses[:n])
    for i in range(n, len(gains)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def kdj(highs, lows, closes, n=9):
    """返回 (K, D, J)。"""
    if len(closes) < n:
        return None, None, None
    k, d = 50.0, 50.0
    for i in range(n - 1, len(closes)):
        window_high = max(highs[i - n + 1:i + 1])
        window_low = min(lows[i - n + 1:i + 1])
        rsv = 50.0 if window_high == window_low else (closes[i] - window_low) / (window_high - window_low) * 100.0
        k = (2.0 * k + rsv) / 3.0
        d = (2.0 * d + k) / 3.0
    return k, d, 3.0 * k - 2.0 * d


def boll(closes, n=20, mult=2.0):
    """返回 (mid, upper, lower)。"""
    if len(closes) < n:
        return None, None, None
    window = closes[-n:]
    mid = statistics.mean(window)
    sd = statistics.pstdev(window)
    return mid, mid + mult * sd, mid - mult * sd


def snapshot_metrics(bar, history):
    """一级快照指标。history 为该标的按时间升序的历史 bar（不含当前）。"""
    close = bar.get("close")
    prev = bar.get("prev_close")
    pct = _pct_change(close, prev)
    limit = BOARD_LIMIT.get(bar.get("board"), 10.0)

    closes = [b.get("close") for b in history] + [close]
    volumes = [b.get("volume") for b in history] + [bar.get("volume")]
    avg_vol = _mean(volumes[-21:-1]) if len(volumes) >= 2 else None
    vol_x = (bar.get("volume") / avg_vol) if (avg_vol and avg_vol > 0) else None

    def change_n(n):
        if len(closes) > n and closes[-1 - n]:
            return _pct_change(closes[-1], closes[-1 - n])
        return None

    return {
        "symbol": bar.get("symbol"),
        "hhmm": bar.get("hhmm"),
        "as_of": bar.get("as_of"),
        "last": close,
        "open": bar.get("open"),
        "high": bar.get("high"),
        "low": bar.get("low"),
        "prev_close": prev,
        "pct_chg": round(pct, 4) if pct is not None else None,
        "pct_chg_1m": round(change_n(1), 4) if change_n(1) is not None else None,
        "pct_chg_3m": round(change_n(3), 4) if change_n(3) is not None else None,
        "pct_chg_5m": round(change_n(5), 4) if change_n(5) is not None else None,
        "amplitude": round((bar.get("high") - bar.get("low")) / prev * 100.0, 4) if prev else None,
        "volume": bar.get("volume"),
        "amount": bar.get("amount"),
        "vol_x": round(vol_x, 4) if vol_x is not None else None,
        "turnover_rate": bar.get("turnover_rate"),
        "volume_ratio": bar.get("volume_ratio"),
        "bid_ask_ratio": bar.get("bid_ask_ratio"),
        "net_inflow": bar.get("net_inflow"),
        "board": bar.get("board"),
        "is_st": bar.get("is_st"),
        "suspended": bar.get("suspended"),
        "list_days": bar.get("list_days"),
        "high_limit": (pct is not None and pct >= limit - 0.3),
        "low_limit": (pct is not None and pct <= -(limit - 0.3)),
        "source": bar.get("source"),
        "adjust": bar.get("adjust"),
    }


def precision_metrics(bars):
    """二级 K 线指标。bars 为该标的截至当前（含当前）按时间升序的 bar 序列。"""
    if not bars:
        return {}
    closes = [b.get("close") for b in bars]
    highs = [b.get("high") for b in bars]
    lows = [b.get("low") for b in bars]
    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)
    dif, dea, hist = macd(closes)
    k, d, j = kdj(highs, lows, closes)
    mid, upper, lower = boll(closes)
    breakout = None
    if len(highs) >= 21:
        breakout = closes[-1] > max(highs[-21:-1])
    return {
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "ma_bull": bool(ma5 and ma10 and ma20 and ma5 > ma10 > ma20),
        "ema12": ema_series(closes, 12)[-1] if closes else None,
        "ema26": ema_series(closes, 26)[-1] if closes else None,
        "macd_dif": dif, "macd_dea": dea, "macd_hist": hist,
        "rsi6": rsi(closes, 6), "rsi12": rsi(closes, 12),
        "kdj_k": k, "kdj_d": d, "kdj_j": j,
        "boll_mid": mid, "boll_upper": upper, "boll_lower": lower,
        "breakout_20d": breakout,
        "bar_count": len(bars),
    }
