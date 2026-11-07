# -*- coding: utf-8 -*-
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared import strategy_core as core  # noqa: E402
from research import rules_engine  # noqa: E402


class TestCompareAndConditions(unittest.TestCase):
    def test_operators(self):
        self.assertTrue(core.compare(5, ">", 3))
        self.assertTrue(core.compare(5, ">=", 5))
        self.assertTrue(core.compare(3, "between", [2, 4]))
        self.assertFalse(core.compare(5, "between", [2, 4]))
        self.assertTrue(core.compare("主板", "in", ["主板", "创业板"]))
        self.assertTrue(core.compare(True, "==", True))
        self.assertFalse(core.compare(1, "not_in", [1, 2]))

    def test_bad_operator_raises(self):
        with self.assertRaises(ValueError):
            core.compare(1, "~=", 1)

    def test_missing_field_is_rejected(self):
        ok, reason = core.evaluate_condition({"a": 1}, {"field": "b", "op": ">", "value": 0})
        self.assertFalse(ok)
        self.assertIn("missing", reason)

    def test_invalid_between_target_is_rejected(self):
        ok, reason = core.evaluate_condition({"a": 1}, {"field": "a", "op": "between", "value": 1})
        self.assertFalse(ok)
        self.assertIn("compare_error", reason)

    def test_and_or_logic(self):
        conds = [{"field": "a", "op": ">", "value": 0}, {"field": "b", "op": "<", "value": 0}]
        self.assertTrue(core.evaluate_group({"a": 1, "b": -1}, conds, "AND")[0])
        self.assertFalse(core.evaluate_group({"a": 1, "b": 1}, conds, "AND")[0])
        self.assertTrue(core.evaluate_group({"a": 1, "b": 1}, conds, "OR")[0])
        self.assertTrue(core.evaluate_group({}, [], "AND")[0])


class TestUniverseAndTime(unittest.TestCase):
    def test_universe_filters(self):
        rules = {"exclude_st": True, "exclude_suspended": True, "exclude_limit_up": True,
                 "boards": ["主板"], "min_list_days": 60}
        ok, reasons = core.check_universe(
            {"is_st": True, "suspended": False, "high_limit": False,
             "board": "主板", "list_days": 100}, rules)
        self.assertFalse(ok)
        self.assertTrue(any("ST" in r for r in reasons))

    def test_board_and_list_days(self):
        rules = {"boards": ["主板"], "min_list_days": 60}
        ok, reasons = core.check_universe({"board": "科创板", "list_days": 30}, rules)
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 2)

    def test_time_window(self):
        windows = [["09:35", "11:25"], ["13:05", "14:50"]]
        self.assertTrue(core.in_time_window("10:30", windows)[0])
        self.assertFalse(core.in_time_window("11:30", windows)[0])
        self.assertTrue(core.in_time_window("15:00", [])[0])
        self.assertFalse(core.in_time_window("bad", windows)[0])


class TestSignalAndRisk(unittest.TestCase):
    def setUp(self):
        self.rules = rules_engine.load_rules()

    def test_signal_id_is_deterministic_and_unique(self):
        metrics = {"last": 10.0, "hhmm": "10:30", "as_of": "20260101 10:30"}
        a = core.make_signal("600000.SH", "buy", "20260101 10:30", self.rules, metrics, [])
        b = core.make_signal("600000.SH", "buy", "20260101 10:30", self.rules, metrics, [])
        c = core.make_signal("600000.SH", "sell", "20260101 10:30", self.rules, metrics, [])
        self.assertEqual(a["client_order_id"], b["client_order_id"])
        self.assertNotEqual(a["client_order_id"], c["client_order_id"])

    def test_evaluate_matches_buy(self):
        snapshot = {"symbol": "600000.SH", "hhmm": "10:30", "as_of": "20260101 10:30",
                    "last": 10.0, "pct_chg": 2.0, "volume_ratio": 1.8, "vol_x": 2.2,
                    "rsi6": 55.0, "board": "主板", "is_st": False, "suspended": False,
                    "high_limit": False, "list_days": 500, "amount": 5e7}
        precision = {"ma_bull": True, "macd_hist": 0.12}
        signal, reasons = core.evaluate("600000.SH", snapshot, precision, self.rules)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["side"], "buy")
        self.assertTrue(reasons)

    def test_evaluate_rejects_st(self):
        snapshot = {"symbol": "600000.SH", "hhmm": "10:30", "as_of": "20260101 10:30",
                    "last": 10.0, "pct_chg": 2.0, "is_st": True}
        signal, reasons = core.evaluate("600000.SH", snapshot, {}, self.rules)
        self.assertIsNone(signal)
        self.assertTrue(any("ST" in r for r in reasons))

    def test_risk_rejects_duplicate_lot_and_cash(self):
        signal = {"symbol": "600000.SH", "side": "buy", "price": 10.0, "qty": 150,
                  "client_order_id": "abc"}
        account = {"cash": 1e6, "total_equity": 1e6, "positions": {}, "position_prices": {},
                   "day_orders": 0, "day_turnover": 0, "day_loss_pct": 0,
                   "seen_ids": ["abc"], "last_signal_minutes": {}, "kill_switch": False}
        ok, reasons = core.check_risk(signal, account, self.rules, "10:30")
        self.assertFalse(ok)
        self.assertTrue(any("duplicate" in r for r in reasons))
        self.assertTrue(any("lot_size" in r for r in reasons))

    def test_risk_rejects_t1_sell(self):
        signal = {"symbol": "600000.SH", "side": "sell", "price": 10.0, "qty": 100,
                  "client_order_id": "xyz"}
        account = {"cash": 1e6, "total_equity": 1e6, "positions": {"600000.SH": 100},
                   "position_prices": {"600000.SH": 10.0}, "day_orders": 0,
                   "day_turnover": 0, "day_loss_pct": 0, "seen_ids": [],
                   "last_signal_minutes": {}, "t1_locked": {"600000.SH": 100},
                   "kill_switch": False}
        ok, reasons = core.check_risk(signal, account, self.rules, "10:30")
        self.assertFalse(ok)
        self.assertTrue(any("T+1" in r for r in reasons))

    def test_kill_switch_blocks(self):
        signal = {"symbol": "600000.SH", "side": "buy", "price": 10.0, "qty": 100,
                  "client_order_id": "kk"}
        account = {"kill_switch": True, "cash": 1e6, "total_equity": 1e6,
                   "positions": {}, "seen_ids": []}
        ok, reasons = core.check_risk(signal, account, self.rules, "10:30")
        self.assertFalse(ok)
        self.assertTrue(any("kill_switch" in r for r in reasons))


if __name__ == "__main__":
    unittest.main()
