# -*- coding: utf-8 -*-
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from research import backtest, data, rules_engine  # noqa: E402


class TestBacktest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = rules_engine.load_rules()
        cls.grouped = data.group_by_symbol(
            data.generate_demo_bars(symbol_count=6, day_count=45, seed=42))

    def test_deterministic_and_shape(self):
        a = backtest.run_backtest(self.grouped, self.rules)
        b = backtest.run_backtest(self.grouped, self.rules)
        self.assertEqual(a["summary"], b["summary"])
        self.assertEqual(a["equity_curve"], b["equity_curve"])
        self.assertTrue(a["is_demo"])
        self.assertGreater(len(a["equity_curve"]), 100)
        self.assertEqual(len(a["stress"]), 4)

    def test_every_event_has_idempotency_id_and_no_duplicates(self):
        result = backtest.run_backtest(self.grouped, self.rules)
        ids = [e["client_order_id"] for e in result["events"]]
        self.assertTrue(all(ids))
        self.assertEqual(len(ids), len(set(ids)))
        for event in result["events"]:
            self.assertIn(event["side"], ("buy", "sell"))
            self.assertGreater(event["qty"], 0)
            self.assertEqual(event["qty"] % 100, 0)

    def test_rejections_carry_reasons(self):
        result = backtest.run_backtest(self.grouped, self.rules)
        for item in result["rejections"]:
            self.assertTrue(item["reasons"])
            self.assertIn(item["stage"], ("risk", "precision"))

    def test_cash_never_negative(self):
        result = backtest.run_backtest(self.grouped, self.rules)
        for event in result["events"]:
            self.assertGreaterEqual(event["cash_after"], 0.0)

    def test_scan_latest(self):
        result = backtest.scan_latest(self.grouped, self.rules)
        self.assertEqual(result["count"], 6)
        self.assertTrue(result["is_demo"])
        for row in result["rows"]:
            self.assertIn("symbol", row)
            self.assertIn("reasons", row)


if __name__ == "__main__":
    unittest.main()
