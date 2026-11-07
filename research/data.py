# -*- coding: utf-8 -*-
"""本地数据层：CSV 读写 + 确定性演示数据生成。

生产输入应为你自己导出的真实历史行情；演示数据一律标注 source=DEMO，
且 demo 命令是显式调用的，不会成为默认生产行为。
"""
import csv
import datetime
import json
import math
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DEMO_CSV = os.path.join(DATA_DIR, "demo_bars.csv")

BAR_FIELDS = [
    "symbol", "date", "hhmm",
    "open", "high", "low", "close", "volume", "amount", "prev_close",
    "turnover_rate", "volume_ratio", "bid_ask_ratio", "net_inflow",
    "board", "is_st", "suspended", "list_days",
    "source", "as_of", "adjust",
]
NUMERIC_FIELDS = set([
    "open", "high", "low", "close", "volume", "amount", "prev_close",
    "turnover_rate", "volume_ratio", "bid_ask_ratio", "net_inflow", "list_days",
])
BOOL_FIELDS = set(["is_st", "suspended"])


def _trading_days(count, start=None):
    """生成连续的工作日（不含节假日校准，仅用于演示）。"""
    day = start or datetime.date(2025, 1, 6)
    result = []
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day)
        day += datetime.timedelta(days=1)
    return result


def generate_demo_bars(symbol_count=12, day_count=90, seed=20261107, bars_per_day=4):
    """确定性合成 OHLCV。返回 list[dict]。"""
    rng = random.Random(seed)
    hours = ["09:45", "10:30", "13:30", "14:30"][:max(1, min(4, bars_per_day))]
    days = _trading_days(day_count)
    rows = []
    for idx in range(symbol_count):
        symbol = "%06d.SH" % (600000 + idx * 7) if idx % 2 == 0 else "%06d.SZ" % (1 + idx * 13)
        board = "科创板" if idx == 3 else ("创业板" if idx in (2, 7) else "主板")
        is_st = idx == 9
        list_days = 90 if idx == 10 else rng.randint(300, 3000)
        price = rng.uniform(9.0, 45.0)
        drift = rng.uniform(-0.0008, 0.0016)
        prev_day_close = price
        for d_pos, day in enumerate(days):
            day_ret = rng.gauss(drift, 0.018)
            if d_pos % 23 == 0 and d_pos > 0:
                day_ret += rng.choice([-0.055, 0.06])
            day_close = max(1.0, prev_day_close * (1.0 + day_ret))
            day_previous = prev_day_close
            for h_pos, hhmm in enumerate(hours):
                weight = float(h_pos + 1) / len(hours)
                target = day_previous + (day_close - day_previous) * weight
                close = max(1.0, target * (1.0 + rng.gauss(0.0, 0.004)))
                spread = abs(close - day_previous) * rng.uniform(0.3, 0.9) + close * 0.003
                high = close + spread
                low = max(0.5, close - spread)
                open_price = day_previous if h_pos == 0 else rows[-1]["close"]
                volume = int(rng.uniform(6e5, 2.4e6) * (1.0 + abs(day_ret) * 8.0))
                amount = volume * close
                suspended = (idx == 8 and d_pos in (12, 13) and h_pos == 0)
                rows.append({
                    "symbol": symbol,
                    "date": day.strftime("%Y%m%d"),
                    "hhmm": hhmm,
                    "open": round(open_price, 3),
                    "high": round(high, 3),
                    "low": round(low, 3),
                    "close": round(close, 3),
                    "volume": volume,
                    "amount": round(amount, 2),
                    "prev_close": round(day_previous, 3),
                    "turnover_rate": round(rng.uniform(0.4, 7.5), 3),
                    "volume_ratio": round(rng.uniform(0.5, 2.6), 3),
                    "bid_ask_ratio": round(rng.uniform(-25.0, 30.0), 2),
                    "net_inflow": round(rng.uniform(-8e7, 9e7), 2),
                    "board": board,
                    "is_st": is_st,
                    "suspended": suspended,
                    "list_days": list_days,
                    "source": "DEMO",
                    "as_of": "%s %s" % (day.strftime("%Y%m%d"), hhmm),
                    "adjust": "none",
                })
            prev_day_close = day_close
    return rows


def write_csv(path, rows):
    """写出 CSV，自动建父目录。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BAR_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def _convert(row):
    out = {}
    for key in BAR_FIELDS:
        raw = row.get(key, "")
        if key in NUMERIC_FIELDS:
            try:
                out[key] = float(raw) if raw not in ("", None) else None
            except (TypeError, ValueError):
                out[key] = None
        elif key in BOOL_FIELDS:
            out[key] = str(raw).strip().lower() in ("true", "1", "yes", "y")
        else:
            out[key] = raw
    return out


def read_csv(path):
    """读取 CSV；缺字段抛错而不是静默补 0。"""
    with open(path, "r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [f for f in BAR_FIELDS if f not in (reader.fieldnames or [])]
        if missing:
            raise ValueError("CSV missing columns: %s" % ", ".join(missing))
        return [_convert(row) for row in reader]


def group_by_symbol(rows):
    """按标的分组并按时间排序。"""
    grouped = {}
    for row in rows:
        grouped.setdefault(row["symbol"], []).append(row)
    for symbol in grouped:
        grouped[symbol].sort(key=lambda r: (r["date"], r["hhmm"], r["as_of"]))
    return grouped


def write_json(path, obj):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)
    return path


def demo_meta(rows):
    """演示数据元信息，页面与报告据此处处标注 DEMO。"""
    symbols = sorted(set(r["symbol"] for r in rows))
    dates = sorted(set(r["date"] for r in rows))
    return {
        "is_demo": True,
        "source": "DEMO / 确定性合成数据",
        "rows": len(rows),
        "symbols": len(symbols),
        "from": dates[0] if dates else "",
        "to": dates[-1] if dates else "",
        "adjust": "none",
        "note": "演示数据不是真实行情，不得作为生产输入或投资依据",
    }
