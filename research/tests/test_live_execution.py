# -*- coding: utf-8 -*-
"""QMT 生产执行层链路测试（用假 ContextInfo，不接触任何券商账户）。"""
import copy
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from research import rules_engine  # noqa: E402
from tools import export_rules  # noqa: E402

export_rules.export(rules_engine.load_rules())

from execution.qmt import strategy, live_config as cfg, broker_api, order_manager  # noqa: E402


class FakeBrokerC(object):
    """模拟浙商证券 QMT 的 ContextInfo：包含账户、全推行情、历史、下单、定时器。"""

    def __init__(self, with_positions=True):
        self.date = "20261109"
        self.hhmm = "10:30"
        self.account_id = "88888888"
        self.calls = []
        self.cancels = []
        self.timers = []
        self.orders = []
        self.stock_list = ["600000.SH", "000001.SZ"]
        self.full_tick = {
            "600000.SH": {
                "lastPrice": 12.0, "lastClose": 11.76,
                "open": 11.8, "high": 12.2, "low": 11.7,
                "volume": 2000000, "amount": 5e7,
                "volumeRatio": 1.8, "vol_x": 2.1,
                "bidVol": [100, 200, 300], "askVol": [100, 100, 100],
                "listDays": 500, "board": "主板",
            },
            "000001.SZ": {
                "lastPrice": 11.5, "lastClose": 12.0,
                "open": 12.0, "high": 12.1, "low": 11.4,
                "volume": 900000, "amount": 2e7,
                "volumeRatio": 0.8, "vol_x": 0.7,
                "listDays": 800, "board": "主板",
            },
        }
        self.account = {
            "cash": 1000000.0,
            "total_equity": 1000000.0,
            "positions": {"000001.SZ": 100} if with_positions else {},
            "position_prices": {"000001.SZ": 11.5} if with_positions else {},
            "position_cost": {"000001.SZ": 12.0} if with_positions else {},
            "high_since_entry": {"000001.SZ": 12.5} if with_positions else {},
            "t1_locked": {},
            "day_orders": 0, "day_turnover": 0.0, "day_loss_pct": 0.0,
        }

    def history(self, code):
        bars = []
        for i in range(70):
            close = 10.0 + 0.06 * i + 0.18 * math.sin(i * 0.9)
            bars.append({"close": close, "high": close + 0.3,
                         "low": close - 0.3, "volume": 1000000})
        return bars

    def get_instrument_detail(self, code):
        return {"OpenDate": "20200101"}

    def run_time(self, func_name, period, start_time=""):
        self.timers.append((func_name, period, start_time))

    def get_full_tick(self, codes):
        return self.full_tick

    def get_stock_list_in_sector(self, sector):
        return list(self.stock_list)

    def get_market_data_ex(self, fields, codes, period="1d", count=80):
        bars = self.history(codes[0])
        return {codes[0]: {"close": [b["close"] for b in bars],
                           "high": [b["high"] for b in bars],
                           "low": [b["low"] for b in bars],
                           "volume": [b["volume"] for b in bars]}}

    def passorder(self, side, symbol, price, qty, cid):
        self.calls.append((side, symbol, price, qty, cid))
        return "BROKER-%s" % cid[:8]

    def cancel(self, broker_order_id):
        self.cancels.append(broker_order_id)


def enable_live():
    """把策略与配置同时切到全自动（仅测试内使用）。"""
    strategy.KILL_SWITCH, strategy.DRY_RUN, strategy.MODE = False, False, "full_auto"
    cfg.KILL_SWITCH, cfg.DRY_RUN, cfg.MODE = False, False, "full_auto"
    cfg.REPORT_CONFIRMED, cfg.LIVE_ACKNOWLEDGED = True, True


class LiveExecutionTest(unittest.TestCase):
    def setUp(self):
        self.saved = {
            "strategy": (strategy.KILL_SWITCH, strategy.DRY_RUN, strategy.MODE,
                         strategy.RULES),
            "cfg": (cfg.KILL_SWITCH, cfg.DRY_RUN, cfg.MODE,
                    cfg.REPORT_CONFIRMED, cfg.LIVE_ACKNOWLEDGED),
        }
        # 只验证「行情 -> 选股 -> 指标 -> 风控 -> 下单」链路是否接通，
        # 不把测试绑死在示例策略的二级阈值上（合成 K 线不保证 RSI/MACD 达标）。
        relaxed = copy.deepcopy(strategy.RULES)
        relaxed["buy"]["precision_conditions"] = [
            {"field": "ma_bull", "op": "==", "value": True}
        ]
        strategy.RULES = relaxed

    def tearDown(self):
        (strategy.KILL_SWITCH, strategy.DRY_RUN, strategy.MODE,
         strategy.RULES) = self.saved["strategy"]
        (cfg.KILL_SWITCH, cfg.DRY_RUN, cfg.MODE,
         cfg.REPORT_CONFIRMED, cfg.LIVE_ACKNOWLEDGED) = self.saved["cfg"]


class TestActivationGate(LiveExecutionTest):
    def test_default_is_fully_blocked(self):
        ok, reasons = cfg.activation_status()
        self.assertFalse(ok)
        self.assertTrue(any("报告" in r for r in reasons))
        self.assertFalse(cfg.live_order_allowed())
        self.assertTrue(cfg.effective_dry_run())

    def test_all_gates_required(self):
        cfg.MODE, cfg.KILL_SWITCH, cfg.DRY_RUN = "full_auto", False, False
        cfg.REPORT_CONFIRMED, cfg.LIVE_ACKNOWLEDGED = True, True
        self.assertTrue(cfg.live_order_allowed())
        cfg.REPORT_CONFIRMED = False
        self.assertFalse(cfg.live_order_allowed())
        self.assertTrue(cfg.effective_dry_run())

    def test_semi_auto_never_allows_orders(self):
        cfg.MODE, cfg.KILL_SWITCH, cfg.DRY_RUN = "semi_auto", False, False
        cfg.REPORT_CONFIRMED, cfg.LIVE_ACKNOWLEDGED = True, True
        self.assertTrue(cfg.activation_status()[0])
        self.assertFalse(cfg.live_order_allowed())


class TestFullChain(LiveExecutionTest):
    def test_full_auto_places_buy_and_sell(self):
        enable_live()
        ctx = FakeBrokerC()
        state = strategy.init(ctx)
        orders = strategy.scan_once(ctx, state, ctx.date, ctx.hhmm, now_ts=1000.0)
        sides = [call[0] for call in ctx.calls]
        self.assertIn("buy", sides, "应产生自动买入委托")
        self.assertIn("sell", sides, "应只针对持仓产生自动卖出委托")
        self.assertEqual(len(ctx.calls), len(orders))
        self.assertTrue(all(o["state"] == "submitted" for o in orders))
        self.assertTrue(state["timer_started"])
        self.assertEqual(ctx.timers[0][0], "on_timer")

    def test_sell_scan_is_position_only(self):
        enable_live()
        ctx = FakeBrokerC(with_positions=False)
        state = strategy.init(ctx)
        strategy.scan_once(ctx, state, ctx.date, ctx.hhmm, now_ts=1000.0)
        self.assertNotIn("sell", [c[0] for c in ctx.calls])

    def test_semi_auto_generates_signals_without_orders(self):
        strategy.KILL_SWITCH, strategy.DRY_RUN, strategy.MODE = False, True, "semi_auto"
        cfg.KILL_SWITCH, cfg.MODE = False, "semi_auto"
        cfg.REPORT_CONFIRMED, cfg.LIVE_ACKNOWLEDGED = True, True
        ctx = FakeBrokerC()
        state = strategy.init(ctx)
        orders = strategy.scan_once(ctx, state, ctx.date, ctx.hhmm, now_ts=1000.0)
        self.assertTrue(orders)
        self.assertEqual(ctx.calls, [], "半自动只出信号，不能调用 passorder")
        self.assertTrue(all(o["state"] == "dry_run" for o in orders))
        self.assertTrue(any(i["kind"] == "signal" for i in state["audit"].dump()))

    def test_kill_switch_stops_everything(self):
        enable_live()
        ctx = FakeBrokerC()
        state = strategy.init(ctx)
        strategy.set_kill_switch(ctx, True, "test")
        self.assertEqual(strategy.scan_once(ctx, state, ctx.date, ctx.hhmm, 1000.0), [])
        self.assertEqual(ctx.calls, [])
        self.assertTrue(any(i["kind"] == "halted" for i in state["audit"].dump()))

    def test_probe_lists_api_and_masks_account(self):
        ctx = FakeBrokerC()
        info = broker_api.probe(ctx)
        self.assertIn("get_full_tick", info["available_api"])
        self.assertTrue(info["account_detected"])
        self.assertIn("*", info["account_id"])
        self.assertNotIn("88888888", info["account_id"])
        status = strategy.status(ctx)
        self.assertIn("*", status["account_id"])
        self.assertFalse(status["config"]["live_order_allowed"])


class TestIndicatorsAndOrders(LiveExecutionTest):
    def test_intraday_minute_indicators(self):
        ctx = FakeBrokerC()
        state = strategy.init(ctx)
        t1 = strategy._normalize_tick(ctx, "600000.SH", ctx.full_tick["600000.SH"],
                                      state, "10:30", ctx.date)
        tick2 = dict(ctx.full_tick["600000.SH"], lastPrice=11.4, volume=2600000)
        strategy._normalize_tick(ctx, "600000.SH", tick2, state, "10:35", ctx.date)
        self.assertIsNotNone(t1["pct_chg"])
        self.assertEqual(t1["board"], "主板")
        self.assertIsNotNone(t1["bid_ask_ratio"])
        minutes = state["minute_price"]["600000.SH"]
        self.assertIn("10:30", minutes)
        self.assertIn("10:35", minutes)

    def test_history_adapter_handles_qmt_dataframe_like_dict(self):
        ctx = FakeBrokerC()
        bars = broker_api.get_history(ctx, "600000.SH")
        self.assertEqual(len(bars), 70)
        self.assertIn("close", bars[0])
        self.assertIn("high", bars[0])

    def test_order_report_and_timeout_cancel(self):
        enable_live()
        ctx = FakeBrokerC()
        state = strategy.init(ctx)
        orders = strategy.scan_once(ctx, state, ctx.date, ctx.hhmm, now_ts=1000.0)
        self.assertTrue(orders)
        pending = [o for o in orders if o["state"] == "submitted"]
        cid = pending[0]["client_order_id"]
        ctx.orders = [{"client_order_id": cid, "state": "filled",
                       "filled_qty": pending[0]["qty"], "qty": pending[0]["qty"]}]
        strategy._NOW_TS = 1001.0
        strategy.poll_orders(ctx, state, "20261109 10:31")
        self.assertEqual(state["orders"].get(cid)["state"], "filled")

        second = FakeBrokerC()
        state2 = strategy.init(second)
        second_orders = strategy.scan_once(second, state2, second.date,
                                           second.hhmm, now_ts=1000.0)
        strategy._NOW_TS = 1000.0 + cfg.ORDER_TIMEOUT_SECONDS + 5
        strategy.poll_orders(second, state2, "20261109 11:01")
        self.assertTrue(second.cancels, "超时未成交委托应被撤销")
        self.assertEqual(len(second_orders), len(second.calls))

    def test_duplicate_scan_same_minute_is_idempotent(self):
        enable_live()
        ctx = FakeBrokerC()
        state = strategy.init(ctx)
        first = strategy.scan_once(ctx, state, ctx.date, ctx.hhmm, 1000.0)
        second = strategy.scan_once(ctx, state, ctx.date, ctx.hhmm, 1003.0)
        self.assertTrue(first)
        self.assertEqual(second, [], "同一分钟内重复扫描不应重复报单")

    def test_rules_snapshot_matches_kernel(self):
        from execution.qmt import rules_snapshot
        import hashlib
        core_path = os.path.join(ROOT, "shared", "strategy_core.py")
        copy_path = os.path.join(ROOT, "execution", "qmt", "strategy_core.py")
        with open(core_path, "rb") as a, open(copy_path, "rb") as b:
            self.assertEqual(hashlib.sha256(a.read()).hexdigest(),
                             hashlib.sha256(b.read()).hexdigest())
        with open(core_path, "rb") as handle:
            core_hash = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(rules_snapshot.CORE_SHA256, core_hash)


if __name__ == "__main__":
    unittest.main()
