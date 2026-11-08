# -*- coding: utf-8 -*-
"""左侧导航工作台：功能清单、白名单动作、文档读取与 HTTP 安全边界。

注意：这里验证的所有动作都只跑本地研究命令，不连券商、不下单、不建实时通道。
"""
from __future__ import unicode_literals

import json
import os
import sys
import unittest
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from research import server  # noqa: E402

# server.run_action("tests") 派生的子进程会带上这个标记；子进程内跳过
# 所有会再次触发 run_action 的用例，防止测试套件自我递归。
IS_ACTION_CHILD = os.environ.get("CLAW_ACTION_CHILD") == "1"


class FeatureCatalogTests(unittest.TestCase):
    def test_catalog_is_consistent(self):
        data = server.features_payload()
        features = data["features"]
        self.assertEqual(len(features), len(server.FEATURES))
        self.assertEqual(data["groups"], list(server.GROUPS))
        ids = [item["id"] for item in features]
        self.assertEqual(len(ids), len(set(ids)), "导航 id 必须唯一")
        for item in features:
            self.assertIn(item["group"], server.GROUPS)
            self.assertIn(item["usage"], ("view", "action", "doc"))
            self.assertTrue(item["label"] and item["desc"])
            if item["usage"] == "action":
                self.assertIn(item["target"], server.ACTION_NAMES)
            if item["usage"] == "doc":
                self.assertIn(item["target"], server.DOC_FILES)

    def test_no_trading_capability_is_exposed(self):
        names = " ".join(server.ACTION_NAMES).lower()
        for word in ("order", "passorder", "broker", "trade", "live", "serve"):
            self.assertNotIn(word, names)
        self.assertTrue(server.features_payload()["note"])

    def test_all_docs_exist(self):
        for name in server.DOC_FILES:
            doc = server.read_doc(name)
            self.assertEqual(doc["name"], name)
            self.assertTrue(doc["content"])
            self.assertTrue(os.path.isfile(doc["path"]))


class SafetyStatusTests(unittest.TestCase):
    def test_gates_are_read_without_importing_qmt(self):
        gates = server._read_gates()
        self.assertEqual(gates.get("BROKER_NAME"), "浙商证券")
        self.assertTrue(gates.get("KILL_SWITCH"))
        self.assertTrue(gates.get("DRY_RUN"))
        self.assertEqual(gates.get("MODE"), "semi_auto")
        self.assertFalse(gates.get("REPORT_CONFIRMED"))
        self.assertEqual(gates.get("ACCOUNT_ID"), "")

    def test_status_reports_all_gates_closed(self):
        status = server.status_payload()
        self.assertFalse(status["live_order_allowed"])
        self.assertFalse(status["account_id_in_code"])
        self.assertTrue(status["read_only"])
        self.assertTrue(status["no_realtime_bridge"])
        self.assertTrue(status["kernel_identical"])
        self.assertTrue(status["snapshot_hash_matches"])
        self.assertTrue(status["rules_digest"])

    def test_doc_whitelist_rejects_unknown_and_traversal(self):
        for name in ("../README.md", "..%2FREADME.md", "docs/INDICATORS.md",
                     "live_config.py", "", None):
            with self.assertRaises((KeyError, TypeError)):
                server.read_doc(name)


class ActionWhitelistTests(unittest.TestCase):
    def test_unknown_action_never_runs(self):
        for name in ("serve", "backtest --rules x", "rm", "passorder", "", None):
            with self.assertRaises((KeyError, TypeError)):
                server.run_action(name)

    @unittest.skipIf(IS_ACTION_CHILD, "动作子进程内跳过，避免递归派生")
    def test_known_action_runs_and_is_logged(self):
        before = len(server.ACTION_LOG)
        result = server.run_action("tests")
        self.assertTrue(result["ok"], result["output"][-500:])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("OK", result["output"])
        self.assertRegex(result["output"], r"Ran \d+ tests")
        self.assertTrue(result["command"])
        self.assertFalse(result["truncated"])
        # 日志有 50 条上限；用「最后一条是本次动作」验证写入，不依赖绝对条数。
        self.assertLessEqual(len(server.ACTION_LOG), 50)
        self.assertGreaterEqual(len(server.ACTION_LOG), min(before + 1, 50))
        self.assertEqual(server.ACTION_LOG[-1]["name"], "tests")

    @unittest.skipIf(IS_ACTION_CHILD, "动作子进程内跳过，避免递归派生")
    def test_action_log_is_bounded(self):
        server.ACTION_LOG.extend([{"ts": "x", "name": "demo", "ok": True,
                                   "exit_code": 0, "duration_ms": 0,
                                   "preview": ""} for _ in range(60)])
        server.run_action("tests")
        self.assertLessEqual(len(server.ACTION_LOG), 50)


class HttpWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = server.start_background("127.0.0.1", 0)
        cls.port = cls.srv.server_address[1]
        cls.base = "http://127.0.0.1:%s" % cls.port
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def request(self, path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=20) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_new_get_endpoints(self):
        code, text = self.request("/api/features")
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(text)["features"]), len(server.FEATURES))
        for path in ("/api/health", "/api/dashboard", "/api/rules",
                     "/api/capabilities", "/api/status", "/api/action-log"):
            code, _ = self.request(path)
            self.assertEqual(code, 200, path)

    def test_static_pages_and_assets(self):
        for path in ("/", "/index.html", "/config.html", "/nav.js",
                     "/app.js", "/config.js", "/style.css"):
            code, _ = self.request(path)
            self.assertEqual(code, 200, path)
        self.assertEqual(self.request("/not_exists.js")[0], 404)

    def test_doc_endpoint(self):
        code, text = self.request("/api/docs?name=INDICATORS")
        self.assertEqual(code, 200)
        self.assertIn("指标", json.loads(text)["content"])
        self.assertEqual(self.request("/api/docs?name=../README.md")[0], 400)
        self.assertEqual(self.request("/api/docs")[0], 400)

    @unittest.skipIf(IS_ACTION_CHILD, "动作子进程内跳过，避免递归派生")
    def test_action_endpoint(self):
        code, text = self.request("/api/actions", "POST", {"name": "tests"})
        self.assertEqual(code, 200)
        payload = json.loads(text)
        self.assertTrue(payload["ok"])
        self.assertIn("OK", payload["output"])
        self.assertEqual(self.request("/api/actions", "POST", {"name": "serve"})[0], 400)
        self.assertEqual(self.request("/api/actions", "POST", {})[0], 400)
        self.assertEqual(self.request("/api/nope", "POST", {})[0], 405)

    def test_rules_endpoint_still_guarded(self):
        rules = json.loads(self.request("/api/rules")[1])["rules"]
        bad = dict(rules)
        bad["account_id"] = "123456"
        self.assertEqual(self.request("/api/rules", "POST", bad)[0], 400)

    def test_non_loopback_refused(self):
        with self.assertRaises(ValueError):
            server.serve("0.0.0.0", 0)


if __name__ == "__main__":
    unittest.main()
