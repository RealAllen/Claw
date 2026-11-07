# -*- coding: utf-8 -*-
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from research import indicators, rules_engine  # noqa: E402


class TestIndicators(unittest.TestCase):
    def test_sma_and_ema(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(indicators.sma(values, 3), 4.0)
        self.assertIsNone(indicators.sma(values, 10))
        series = indicators.ema_series(values, 3)
        self.assertEqual(len(series), 5)
        self.assertAlmostEqual(series[0], 1.0)

    def test_macd_needs_enough_data(self):
        self.assertEqual(indicators.macd([1.0] * 10), (None, None, None))
        closes = [10.0 + i * 0.3 for i in range(60)]
        dif, dea, hist = indicators.macd(closes)
        self.assertIsNotNone(dif)
        self.assertIsNotNone(dea)
        self.assertGreater(hist, 0)

    def test_rsi_bounds(self):
        rising = [10.0 + i for i in range(30)]
        falling = [40.0 - i for i in range(30)]
        self.assertGreater(indicators.rsi(rising, 6), 90.0)
        self.assertLess(indicators.rsi(falling, 6), 10.0)

    def test_kdj_and_boll(self):
        closes = [10.0 + (i % 7) * 0.2 for i in range(60)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        k, d, j = indicators.kdj(highs, lows, closes)
        self.assertIsNotNone(k)
        self.assertIsNotNone(j)
        mid, upper, lower = indicators.boll(closes)
        self.assertTrue(lower < mid < upper)

    def test_snapshot_metrics(self):
        bar = {"symbol": "600000.SH", "close": 11.0, "prev_close": 10.0,
               "open": 10.2, "high": 11.2, "low": 10.1, "volume": 2000000,
               "amount": 2.2e7, "board": "主板", "is_st": False,
               "suspended": False, "list_days": 500, "source": "DEMO",
               "as_of": "20260101 10:30", "hhmm": "10:30", "adjust": "none"}
        history = []
        for i in range(25):
            history.append(dict(bar, close=10.0 + i * 0.01, volume=1000000,
                                amount=1.0e7, as_of="202512%02d 10:30" % (i + 1)))
        metrics = indicators.snapshot_metrics(bar, history)
        self.assertAlmostEqual(metrics["pct_chg"], 10.0, places=3)
        self.assertTrue(metrics["high_limit"])
        self.assertFalse(metrics["low_limit"])
        self.assertAlmostEqual(metrics["vol_x"], 2.0, places=3)


class TestRulesEngine(unittest.TestCase):
    def test_load_and_digest(self):
        rules = rules_engine.load_rules()
        self.assertTrue(rules_engine.validate_rules(rules))
        digest = rules_engine.rule_digest(rules)
        clone = copy.deepcopy(rules)
        clone.pop("_source_path", None)
        self.assertEqual(len(digest), 64)
        self.assertEqual(rules_engine.rule_digest(rules), rules_engine.rule_digest(clone))

    def test_validate_rejects_bad_operator(self):
        rules = rules_engine.load_rules()
        bad = copy.deepcopy(rules)
        bad["buy"]["conditions"][0]["op"] = "~="
        with self.assertRaises(ValueError):
            rules_engine.validate_rules(bad)

    def test_validate_rejects_bad_time_window(self):
        rules = rules_engine.load_rules()
        bad = copy.deepcopy(rules)
        bad["time_windows"] = [["9:35", "11:25"]]
        with self.assertRaises(ValueError):
            rules_engine.validate_rules(bad)

    def test_two_level_precision_is_lazy(self):
        rules = rules_engine.load_rules()
        calls = []

        def provider(symbol):
            calls.append(symbol)
            return {"ma_bull": True, "macd_hist": 0.1}

        flat = {"hhmm": "10:30", "as_of": "20260101 10:30", "board": "主板",
                "is_st": False, "suspended": False, "high_limit": False,
                "list_days": 500, "amount": 5e7, "pct_chg": -1.0,
                "volume_ratio": 0.1, "vol_x": 0.1, "rsi6": 10.0, "last": 10.0}
        signal, reasons = rules_engine.scan_symbol("600000.SH", flat, provider, rules)
        self.assertIsNone(signal)
        self.assertEqual(calls, [])

        good = dict(flat, pct_chg=2.0, volume_ratio=1.8, vol_x=2.2, rsi6=55.0)
        signal, reasons = rules_engine.scan_symbol("600000.SH", good, provider, rules)
        self.assertEqual(calls, ["600000.SH"])
        self.assertIsNotNone(signal)


if __name__ == "__main__":
    unittest.main()
