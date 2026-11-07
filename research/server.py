# -*- coding: utf-8 -*-
"""仅绑定 127.0.0.1 的本地看板与策略配置台（Python 标准库 http.server）。

安全约束：
- 非回环地址直接拒绝启动；
- 唯一的写接口是 POST /api/rules，只写本地规则 JSON，绝不写券商/账户/下单相关内容；
- 不提供任何下单接口、不建立任何与 QMT 的实时通道（禁 HTTP/socket/文件桥接）；
- 静态文件限制在 research/web 目录内，防目录穿越；
- 无外部 CDN、无外网依赖。
"""
from __future__ import division

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from research import backtest, data, rules_engine  # noqa: E402

WEB_DIR = os.path.join(_ROOT, "research", "web")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}
_CACHE = {"dashboard": None, "signature": None}

MAX_RULES_BYTES = 262144
FORBIDDEN_RULE_KEYS = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "credential",
    "account_id", "fund_account", "passorder", "cancel_order", "socket", "http",
    "url", "endpoint", "webhook", "broker_api", "trade_api",
)

# 配置台可用的指标字段。一级=全推快照可批量算；二级=候选池逐只算 K 线；持仓=卖出专用。
RULE_FIELDS = {
    "level1": [
        {"key": "pct_chg", "label": "涨跌幅(%)"},
        {"key": "pct_chg_1m", "label": "1分钟涨跌幅(%)"},
        {"key": "pct_chg_3m", "label": "3分钟涨跌幅(%)"},
        {"key": "pct_chg_5m", "label": "5分钟涨跌幅(%)"},
        {"key": "amplitude", "label": "振幅(%)"},
        {"key": "volume_ratio", "label": "量比 / 放量代理"},
        {"key": "vol_x", "label": "放量倍数"},
        {"key": "turnover_rate", "label": "换手率(%)"},
        {"key": "bid_ask_ratio", "label": "委比(%)"},
        {"key": "net_inflow", "label": "资金净流入(元)"},
        {"key": "amount", "label": "成交额(元)"},
        {"key": "last", "label": "最新价"},
    ],
    "level2": [
        {"key": "ma_bull", "label": "均线多头排列(bool)"},
        {"key": "ma5", "label": "MA5"}, {"key": "ma10", "label": "MA10"},
        {"key": "ma20", "label": "MA20"}, {"key": "ma60", "label": "MA60"},
        {"key": "macd_hist", "label": "MACD 柱"},
        {"key": "macd_dif", "label": "MACD DIF"},
        {"key": "macd_dea", "label": "MACD DEA"},
        {"key": "rsi6", "label": "RSI6"}, {"key": "rsi12", "label": "RSI12"},
        {"key": "kdj_k", "label": "KDJ K"}, {"key": "kdj_d", "label": "KDJ D"},
        {"key": "kdj_j", "label": "KDJ J"},
        {"key": "boll_upper", "label": "BOLL 上轨"},
        {"key": "boll_lower", "label": "BOLL 下轨"},
        {"key": "breakout_20d", "label": "突破20日高点(bool)"},
        {"key": "bar_count", "label": "K线根数"},
    ],
    "position": [
        {"key": "pnl_pct", "label": "持仓盈亏(%)"},
        {"key": "pnl_amount", "label": "持仓盈亏(元)"},
        {"key": "drawdown_from_high_pct", "label": "自最高点回撤(%)"},
        {"key": "hold_minutes", "label": "持有分钟数"},
        {"key": "available_qty", "label": "可卖数量"},
        {"key": "held_qty", "label": "持仓数量"},
        {"key": "cost_price", "label": "成本价"},
    ],
}

OPERATORS = [
    {"key": ">", "label": "大于"}, {"key": ">=", "label": "大于等于"},
    {"key": "<", "label": "小于"}, {"key": "<=", "label": "小于等于"},
    {"key": "==", "label": "等于"}, {"key": "!=", "label": "不等于"},
    {"key": "between", "label": "区间 [低, 高]"},
]

# 能力归属：把用户原始目标逐项对应到实际实现位置，避免误以为本地网页会实时下单。
CAPABILITIES = [
    {"goal": "使用我的账户", "where": "浙商证券标准版 QMT 客户端（本机登录）",
     "how": "账户凭据由券商终端加密保管；策略只通过沙箱 API 读取资金/持仓并下单，代码与日志无密码",
     "in_local_web": False},
    {"goal": "盘中实时选股", "where": "QMT 内置 Python 沙箱 · on_timer 定时器",
     "how": "get_full_tick 全市场 L1 快照，每 3 秒一轮，一级快照指标粗筛全市场",
     "in_local_web": False},
    {"goal": "分析各项指标", "where": "QMT 沙箱 · 两级筛选",
     "how": "快照指标 + 候选池 K 线指标 + 持仓盈亏指标，规则 DSL 逐条判定并留审计",
     "in_local_web": "盘前编辑 / 盘后复盘"},
    {"goal": "满足条件自动买卖", "where": "QMT 沙箱 · 风控 → 幂等 → passorder",
     "how": "买入扫全市场、卖出只扫持仓；风控硬前置、去重、回报轮询、超时撤单、对账熔断",
     "in_local_web": False},
]


def _csv_path():
    return os.environ.get("CLAW_DATA_CSV", data.DEMO_CSV)


def build_dashboard(rules_path=None, csv_path=None):
    """组装看板数据。带简单缓存，避免每次请求都重跑回测。"""
    csv_path = csv_path or _csv_path()
    if not os.path.isfile(csv_path):
        rows = data.generate_demo_bars()
        data.write_csv(csv_path, rows)
    rules_file = os.path.abspath(rules_path or rules_engine.DEFAULT_RULES)
    signature = (os.path.abspath(csv_path), os.path.getmtime(csv_path),
                 rules_file, os.path.getmtime(rules_file) if os.path.isfile(rules_file) else 0)
    if _CACHE["dashboard"] is not None and _CACHE["signature"] == signature:
        return _CACHE["dashboard"]
    rules = rules_engine.load_rules(rules_path)
    grouped = data.group_by_symbol(data.read_csv(csv_path))
    result = backtest.run_backtest(grouped, rules)
    scan = backtest.scan_latest(grouped, rules)
    meta = data.demo_meta([row for bars in grouped.values() for row in bars])
    payload = {
        "meta": meta,
        "rules": result["rules"],
        "summary": result["summary"],
        "equity_curve": result["equity_curve"],
        "events": result["events"][:80],
        "rejections": result["rejections"][:80],
        "event_count": len(result["events"]),
        "rejection_count": len(result["rejections"]),
        "stress": result["stress"],
        "scan": scan["rows"],
        "scan_signals": scan["signals"],
        "final_positions": result["final_positions"],
        "assumptions": result["assumptions"],
        "capabilities": CAPABILITIES,
        "safety": {
            "read_only": True,
            "kill_switch": True,
            "mode": rules.get("mode", "semi_auto"),
            "bind": "127.0.0.1",
            "credentials_in_app": False,
        },
    }
    _CACHE["dashboard"] = payload
    _CACHE["signature"] = signature
    return payload


def rules_payload():
    """配置台数据：当前规则 + 可比对的指标字段字典 + 能力归属说明。"""
    rules = rules_engine.load_rules()
    return {
        "rules": rules,
        "digest": rules_engine.rule_digest(rules),
        "path": os.path.abspath(rules_engine.DEFAULT_RULES),
        "fields": RULE_FIELDS,
        "operators": OPERATORS,
        "capabilities": CAPABILITIES,
        "write_scope": "仅写本地 shared/rules/live.json；不含任何账户、凭据或下单能力",
    }


def _scan_forbidden(value):
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(bad in lowered for bad in FORBIDDEN_RULE_KEYS):
                return str(key)
            found = _scan_forbidden(item)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _scan_forbidden(item)
            if found:
                return found
    return None


def save_rules(payload):
    """保存本地规则 JSON。固定路径、原子写入、结构校验、拒绝凭据类字段。"""
    if not isinstance(payload, dict):
        raise ValueError("规则必须是 JSON 对象")
    bad = _scan_forbidden(payload)
    if bad:
        raise ValueError("规则里不允许出现账户/凭据/下单相关字段: %s" % bad)
    rules_engine.validate_rules(payload)
    path = os.path.abspath(rules_engine.DEFAULT_RULES)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)
    _CACHE["dashboard"] = None
    return {"ok": True, "digest": rules_engine.rule_digest(payload), "path": path}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ClawLocal/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[claw] %s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, body, content_type):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._send(200, json.dumps({"ok": True, "bind": self.server.server_address[0]}),
                              "application/json; charset=utf-8")
        if path == "/api/dashboard":
            try:
                payload = build_dashboard()
                return self._send(200, json.dumps(payload, ensure_ascii=False),
                                  "application/json; charset=utf-8")
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}, ensure_ascii=False),
                                  "application/json; charset=utf-8")
        if path == "/api/rules":
            try:
                return self._send(200, json.dumps(rules_payload(), ensure_ascii=False),
                                  "application/json; charset=utf-8")
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}, ensure_ascii=False),
                                  "application/json; charset=utf-8")
        if path == "/api/capabilities":
            return self._send(200, json.dumps({"capabilities": CAPABILITIES}, ensure_ascii=False),
                              "application/json; charset=utf-8")
        name = "index.html" if path in ("/", "") else os.path.basename(path)
        full = os.path.abspath(os.path.join(WEB_DIR, name))
        if not full.startswith(os.path.abspath(WEB_DIR)) or not os.path.isfile(full):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as handle:
            return self._send(200, handle.read(), CONTENT_TYPES.get(ext, "application/octet-stream"))

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/rules":
            return self._send(405, "method not allowed", "text/plain; charset=utf-8")
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except (TypeError, ValueError):
            return self._send(400, "invalid content-length", "text/plain; charset=utf-8")
        if length <= 0 or length > MAX_RULES_BYTES:
            return self._send(413, "payload too large", "text/plain; charset=utf-8")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            return self._send(400, json.dumps({"error": "invalid json: %s" % exc}, ensure_ascii=False),
                              "application/json; charset=utf-8")
        try:
            result = save_rules(payload)
        except Exception as exc:
            return self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False),
                              "application/json; charset=utf-8")
        return self._send(200, json.dumps(result, ensure_ascii=False),
                          "application/json; charset=utf-8")


def serve(host="127.0.0.1", port=8765):
    if host not in LOOPBACK:
        raise ValueError("refusing to bind non-loopback address: %s" % host)
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    server.daemon_threads = True
    sys.stderr.write("[claw] local dashboard at http://%s:%s/ (read-only)\n" % (host, port))
    return server


def start_background(host="127.0.0.1", port=8765):
    server = serve(host, port)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server
