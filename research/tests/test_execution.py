# -*- coding: utf-8 -*-
import copy
import hashlib
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from research import indicators, rules_engine  # noqa: E402
from tools import export_rules  # noqa: E402

RULES = rules_engine.load_rules()
export_rules.export(RULES)

from execution.qmt import (strategy, order_manager, reconcile)  # noqa: E402

QMT_DIR = os.path.join(ROOT, "execution", "qmt")


class FakeC(object):
    def __init__(self, with_passorder=False):
        self.date = "20260102"
        self.hhmm = "10:30"
        self.calls = []
        self.full_tick = {
            "600000.SH": {
                "lastPrice": 12.0, "lastClose": 11.76,
                "open": 11.8, "high": 12.2, "low": 11.7,
                "volume": 2000000, "amount": 5e7,
                "volumeRatio": 1.8, "vol_x": 2.1,
                "board": "主板", "isST": False, "suspended": False,
                "listDays": 500,
            }
        }
        self.account = {
            "cash": 1000000.0, "total_equity": 1000000.0,
            "positions": {}, "position_prices": {},
            "day_orders": 0, "day_turnover": 0.0, "day_loss_pct": 0.0,
            "t1_locked": {},
        }
        self._with_passorder = with_passorder
        self.history = self._history

    def _history(self, code):
        bars = []
        for i in range(70):
            close = 10.0 + 0.06 * i + 0.18 * math.sin(i * 0.9)
            bars.append({"close": close, "high": close + 0.3,
                         "low": close - 0.3, "volume": 1000000})
        return bars

    def passorder(self, side, symbol, price, qty, cid):
        self.calls.append((side, symbol, price, qty, cid))
        return "BROKER-0001"


class TestExportBundle(unittest.TestCase):
    def test_generated_files(self):
        for name in ("rules_snapshot.py", "strategy_core.py", "indicators.py"):
            self.assertTrue(os.path.isfile(os.path.join(QMT_DIR, name)), name)

    def test_core_copy_matches_source(self):
        def sha(path):
            with open(path, "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(sha(os.path.join(ROOT, "shared", "strategy_core.py")),
                         sha(os.path.join(QMT_DIR, "strategy_core.py")))
        self.assertEqual(sha(os.path.join(ROOT, "research", "indicators.py")),
                         sha(os.path.join(QMT_DIR, "indicators.py")))

    def test_bundle_rules(self):
        from execution.qmt import rules_snapshot
        self.assertEqual(rules_snapshot.RULES["version"], RULES["version"])
        self.assertEqual(len(rules_snapshot.RULE_DIGEST), 64)


class TestOrderManager(unittest.TestCase):
    def _signal(self, cid="id-1"):
        return {"client_order_id": cid, "symbol": "600000.SH", "side": "buy",
                "price": 12.0, "qty": 100}

    def test_kill_switch_blocks(self):
        mgr = order_manager.OrderManager(dry_run=False, kill_switch=True)
        order, reason = mgr.submit(self._signal(), FakeC(), "t")
        self.assertIsNone(order)
        self.assertIn("kill_switch", reason)

    def test_dry_run_never_calls_passorder(self):
        ctx = FakeC(with_passorder=True)
        mgr = order_manager.OrderManager(dry_run=True, kill_switch=False)
        order, reason = mgr.submit(self._signal(), ctx, "t")
        self.assertEqual(order["state"], "dry_run")
        self.assertEqual(ctx.calls, [])

    def test_duplicate_id_blocked(self):
        ctx = FakeC()
        mgr = order_manager.OrderManager(dry_run=True, kill_switch=False)
        mgr.submit(self._signal("dup"), ctx, "t")
        order, reason = mgr.submit(self._signal("dup"), ctx, "t")
        self.assertIsNone(order)
        self.assertIn("duplicate", reason)

    def test_real_call_only_when_both_switches_off(self):
        ctx = FakeC(with_passorder=True)
        mgr = order_manager.OrderManager(dry_run=False, kill_switch=False)
        order, reason = mgr.submit(self._signal("real"), ctx, "t")
        self.assertEqual(order["state"], "submitted")
        self.assertEqual(len(ctx.calls), 1)
        mgr.on_report("real", "filled", "t+1", filled_qty=100)
        self.assertEqual(mgr.get("real")["state"], "filled")
        self.assertEqual(len(mgr.unconfirmed()), 0)


class TestReconcile(unittest.TestCase):
    def test_no_diff(self):
        self.assertEqual(reconcile.reconcile({"A": 100}, {"A": 100}, 100.0, 100.0), [])

    def test_diff_triggers_halt(self):
        diffs = reconcile.reconcile({"A": 100}, {"A": 90}, 100.0, 101.0)
        self.assertEqual(len(diffs), 2)
        self.assertTrue(reconcile.should_halt(diffs))


class TestStrategyTemplate(unittest.TestCase):
    def setUp(self):
        self._kill, self._dry = strategy.KILL_SWITCH, strategy.DRY_RUN
        self._rules = strategy.RULES

    def tearDown(self):
        strategy.KILL_SWITCH, strategy.DRY_RUN = self._kill, self._dry
        strategy.RULES = self._rules

    def test_default_is_halted(self):
        strategy.KILL_SWITCH, strategy.DRY_RUN = True, True
        ctx = FakeC(with_passorder=True)
        state = strategy.init(ctx)
        self.assertEqual(strategy.scan_once(ctx, state, ctx.date, ctx.hhmm), [])
        self.assertEqual(ctx.calls, [])
        self.assertTrue(any(i["kind"] == "halted" for i in state["audit"].dump()))

    def test_dry_run_scan_produces_order_without_broker_call(self):
        # 布线验证：放宽二级条件，只验证「一级命中 -> 精算 -> 风控 -> dry-run 下单」
        # 的链路是否接通，避免把测试绑死在示例策略的具体阈值上。
        simplified = copy.deepcopy(strategy.RULES)
        simplified["buy"]["precision_conditions"] = [
            {"field": "ma_bull", "op": "==", "value": True}
        ]
        strategy.RULES = simplified
        strategy.KILL_SWITCH, strategy.DRY_RUN = False, True
        ctx = FakeC(with_passorder=True)
        state = strategy.init(ctx)
        orders = strategy.scan_once(ctx, state, ctx.date, ctx.hhmm)
        self.assertEqual(ctx.calls, [])
        self.assertTrue(orders, "expected at least one dry-run order; precision metrics: %s" %
                        indicators.precision_metrics(ctx._history("600000.SH")))
        self.assertTrue(all(o["state"] == "dry_run" for o in orders))
        self.assertTrue(any(i["kind"] == "signal" for i in state["audit"].dump()))

    def test_reconcile_diff_halts_strategy(self):
        strategy.KILL_SWITCH, strategy.DRY_RUN = False, True
        ctx = FakeC()
        state = strategy.init(ctx)
        diffs = strategy.check_reconciliation(ctx, {"600000.SH": 100}, 900000.0)
        self.assertTrue(diffs)
        self.assertTrue(state["halt"])
        self.assertEqual(strategy.scan_once(ctx, state, ctx.date, ctx.hhmm), [])


if __name__ == "__main__":
    unittest.main()
