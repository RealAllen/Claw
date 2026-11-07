# -*- coding: utf-8 -*-
"""无券商环境下的 QMT 执行层演练（假 ContextInfo）。

用途：在不安装 QMT、不登录账户、不联网的情况下，验证
「实时全推行情 -> 全市场选股 -> 指标判定 -> 风控 -> 自动买卖 -> 回报轮询」
这条链路是否接通，并打印每轮结果。

⚠️ 所有调用都落在内存里的假对象上，绝不连接券商、绝不发出真实委托、
绝不写文件、绝不访问网络。演练结束会完整恢复所有安全开关。
"""
from __future__ import division

import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from execution.qmt import strategy, live_config as cfg  # noqa: E402


class SimulatedQMTContext(object):
    """只实现 QMT 常用接口的替身；passorder 只记录调用，不连券商。"""

    def __init__(self):
        self.account_id = "SIM-ACCOUNT"
        self.date = "20261109"
        self.hhmm = "09:40"
        self.calls = []
        self.cancels = []
        self.timers = []
        self.orders = []
        self.stock_list = ["600000.SH", "000001.SZ", "300750.SZ"]
        self.full_tick = {
            "600000.SH": {"lastPrice": 12.0, "lastClose": 11.76,
                          "open": 11.8, "high": 12.2, "low": 11.7,
                          "volume": 2000000, "amount": 5e7,
                          "volumeRatio": 1.8, "vol_x": 2.1,
                          "bidVol": [100, 200, 300], "askVol": [100, 100, 100],
                          "listDays": 500},
            "000001.SZ": {"lastPrice": 11.5, "lastClose": 12.0,
                          "open": 12.0, "high": 12.1, "low": 11.4,
                          "volume": 900000, "amount": 2e7,
                          "volumeRatio": 0.8, "vol_x": 0.7, "listDays": 800},
            "300750.SZ": {"lastPrice": 20.5, "lastClose": 20.0,
                          "open": 20.1, "high": 20.7, "low": 20.0,
                          "volume": 1500000, "amount": 9e7,
                          "volumeRatio": 2.2, "vol_x": 2.6, "listDays": 900},
        }
        self.account = {
            "cash": 1000000.0, "total_equity": 1000000.0,
            "positions": {"000001.SZ": 100},
            "position_prices": {"000001.SZ": 11.5},
            "position_cost": {"000001.SZ": 12.0},
            "high_since_entry": {"000001.SZ": 12.5},
            "t1_locked": {}, "day_orders": 0,
            "day_turnover": 0.0, "day_loss_pct": 0.0,
        }

    def history(self, code):
        bars = []
        for i in range(70):
            close = 10.0 + 0.06 * i + 0.18 * math.sin(i * 0.9)
            bars.append({"close": close, "high": close + 0.3,
                         "low": close - 0.3, "volume": 1000000})
        return bars

    def get_stock_list_in_sector(self, sector):
        return list(self.stock_list)

    def get_full_tick(self, codes):
        return self.full_tick

    def get_market_data_ex(self, fields, codes, period="1d", count=80):
        bars = self.history(codes[0])
        return {codes[0]: {"close": [b["close"] for b in bars],
                           "high": [b["high"] for b in bars],
                           "low": [b["low"] for b in bars],
                           "volume": [b["volume"] for b in bars]}}

    def get_instrument_detail(self, code):
        return {"OpenDate": "20200101"}

    def run_time(self, func_name, period, start_time=""):
        self.timers.append((func_name, period, start_time))

    def passorder(self, side, symbol, price, qty, cid):
        self.calls.append((side, symbol, price, qty, cid))
        return "SIM-%s" % cid[:8]

    def cancel(self, broker_order_id):
        self.cancels.append(broker_order_id)


def run_demo(print_fn=None):
    """跑 3 个时点的完整实时回路，返回统计 dict。"""
    out = print_fn or (lambda *args: None)
    saved = {
        "strategy": (strategy.KILL_SWITCH, strategy.DRY_RUN, strategy.MODE, strategy.RULES),
        "cfg": (cfg.KILL_SWITCH, cfg.DRY_RUN, cfg.MODE,
                cfg.REPORT_CONFIRMED, cfg.LIVE_ACKNOWLEDGED),
    }
    ctx = SimulatedQMTContext()
    try:
        # 演练里临时打开所有闸门，让自动下单路径真正走一遍；
        # 但 passorder 落在本文件的假对象上，退出时完整恢复默认值。
        import copy
        relaxed = copy.deepcopy(strategy.RULES)
        relaxed["buy"]["precision_conditions"] = [
            {"field": "ma_bull", "op": "==", "value": True}
        ]
        strategy.RULES = relaxed
        strategy.KILL_SWITCH, strategy.DRY_RUN, strategy.MODE = False, False, "full_auto"
        cfg.KILL_SWITCH, cfg.DRY_RUN, cfg.MODE = False, False, "full_auto"
        cfg.REPORT_CONFIRMED, cfg.LIVE_ACKNOWLEDGED = True, True

        state = strategy.init(ctx)
        rounds = [("09:40", 100.0), ("10:30", 400.0), ("13:10", 900.0)]
        stats = {"rounds": [], "orders": 0, "broker_calls": 0,
                 "signals": [], "rejects": 0, "halted": state["halt"]}
        for index, (hhmm, ts) in enumerate(rounds):
            if index:
                # 制造盘中价格变化，验证 1/3/5 分钟涨跌幅与幂等去重
                tick = dict(ctx.full_tick["600000.SH"])
                tick["lastPrice"] = 12.0 + index * 0.06
                tick["volume"] = 2000000 + index * 120000
                ctx.full_tick["600000.SH"] = tick
            orders = strategy.scan_once(ctx, state, ctx.date, hhmm, ts)
            # 模拟券商成交回报，并按成交更新假账户持仓（含 T+1 锁定），
            # 避免同一持仓在不同时点被重复卖出，让演练贴近真实链路。
            for order in orders:
                ctx.orders.append({"client_order_id": order["client_order_id"],
                                   "state": "filled", "qty": order["qty"],
                                   "filled_qty": order["qty"]})
            strategy.poll_orders(ctx, state, "%s %s" % (ctx.date, hhmm))
            for order in orders:
                code, qty = order["symbol"], order["qty"]
                positions = ctx.account["positions"]
                if order["side"] == "buy":
                    positions[code] = positions.get(code, 0) + qty
                    ctx.account["position_cost"][code] = order["price"]
                    ctx.account["position_prices"][code] = order["price"]
                    ctx.account["high_since_entry"][code] = order["price"]
                    ctx.account["t1_locked"][code] = ctx.account["t1_locked"].get(code, 0) + qty
                else:
                    positions[code] = max(0, positions.get(code, 0) - qty)
                    if positions[code] == 0:
                        for key in ("positions", "position_cost", "position_prices",
                                    "high_since_entry", "t1_locked"):
                            ctx.account[key].pop(code, None)
            signals = [i for i in state["audit"].dump() if i["kind"] == "signal"]
            rejects = [i for i in state["audit"].dump() if i["kind"] == "risk_reject"]
            stats["rounds"].append({"hhmm": hhmm, "orders": len(orders),
                                    "signals": len(signals), "rejects": len(rejects)})
            stats["signals"] = [i["payload"] for i in signals]
            stats["rejects"] = len(rejects)
            out("[sim] %s 扫描：全市场 %s 只 / 本轮委托 %s 笔 / 累计信号 %s / 风控拒单 %s" %
                (hhmm, len(ctx.stock_list), len(orders), len(signals), len(rejects)))
        stats["orders"] = sum(r["orders"] for r in stats["rounds"])
        stats["broker_calls"] = len(ctx.calls)
        stats["timer"] = ctx.timers
        stats["status"] = strategy.status(ctx)
        for call in ctx.calls:
            out("[sim]   %s %s %s 股 @ %s  幂等ID=%s" %
                (call[0], call[1], call[3], call[2], call[4]))
        out("[sim] 模拟 passorder 调用 %s 次，全部落在内存假对象上，未连接任何券商。" %
            stats["broker_calls"])
        return stats
    finally:
        (strategy.KILL_SWITCH, strategy.DRY_RUN, strategy.MODE,
         strategy.RULES) = saved["strategy"]
        (cfg.KILL_SWITCH, cfg.DRY_RUN, cfg.MODE,
         cfg.REPORT_CONFIRMED, cfg.LIVE_ACKNOWLEDGED) = saved["cfg"]


if __name__ == "__main__":
    result = run_demo(print)
    print("[sim] 汇总：%s" % result["rounds"])
