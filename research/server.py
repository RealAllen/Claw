# -*- coding: utf-8 -*-
"""仅绑定 127.0.0.1 的本地工作台服务（Python 标准库 http.server）。

安全约束：
- 非回环地址直接拒绝启动；
- 唯一的规则写接口是 POST /api/rules，只写本地规则 JSON；
- POST /api/actions 只允许固定白名单里的本地研究命令（无参数、无 shell）；
- 不提供任何下单接口、不读取账户、不建立与 QMT 的实时通道（禁 HTTP/socket/文件桥接）；
- 静态文件限制在 research/web 目录内，文档读取限制在白名单内，防目录穿越；
- 无外部 CDN、无外网依赖。
"""
from __future__ import division

import ast
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

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
MAX_ACTION_BYTES = 4096
ACTION_TIMEOUT = 90
MAX_OUTPUT_CHARS = 20000
MAX_DOC_CHARS = 200000
ACTION_LOCK = threading.Lock()
ACTION_LOG = []
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

# ---------------------------------------------------------------- 功能导航信息架构
# usage: view=切换到本地视图 / action=执行白名单本地命令 / doc=阅读白名单文档
GROUPS = ("工作台", "研究与演练", "执行层", "文档与合规", "系统")
FEATURES = [
    {"id": "overview", "group": "工作台", "label": "功能总览", "usage": "view", "target": "overview",
     "layer": "本地", "desc": "全部功能按分组梳理，标注运行层级与使用方式，可从这里直达任一功能。"},
    {"id": "config", "group": "工作台", "label": "策略配置台", "usage": "view", "target": "config",
     "layer": "本地", "desc": "可视化编辑指标、阈值、标的池、时间窗与风控硬前置，保存为本地规则 JSON。"},
    {"id": "review", "group": "工作台", "label": "模拟盘复盘", "usage": "view", "target": "review",
     "layer": "本地", "desc": "净值曲线、绩效 KPI、成交信号流水、风控拦截与压力测试。"},
    {"id": "scan-view", "group": "工作台", "label": "最新扫描结果", "usage": "view", "target": "scan",
     "layer": "本地", "desc": "最近一根 bar 的全市场扫描明细与命中信号。"},
    {"id": "capabilities", "group": "工作台", "label": "能力归属", "usage": "view", "target": "cap",
     "layer": "两层", "desc": "「账户 / 实时选股 / 指标 / 自动买卖」四项能力分别运行在哪一层。"},

    {"id": "demo", "group": "研究与演练", "label": "生成演示数据", "usage": "action", "target": "demo",
     "layer": "本地", "desc": "重新生成确定性合成行情与离线看板数据（明确标注 DEMO）。"},
    {"id": "backtest", "group": "研究与演练", "label": "运行回测", "usage": "action", "target": "backtest",
     "layer": "本地", "desc": "按当前规则跑 T+1 / 费用 / 滑点模拟盘，输出绩效与明细 JSON。"},
    {"id": "scan", "group": "研究与演练", "label": "扫描最新 bar", "usage": "action", "target": "scan",
     "layer": "本地", "desc": "对最新一根 bar 做一次全市场两级筛选，输出命中信号。"},
    {"id": "simulate", "group": "研究与演练", "label": "无券商全链路演练", "usage": "action", "target": "qmt-sim",
     "layer": "本地", "desc": "假 ContextInfo 跑完整自动买卖链路，不连账户、不发真实委托。"},
    {"id": "export", "group": "研究与演练", "label": "导出沙箱规则", "usage": "action", "target": "export",
     "layer": "本地", "desc": "生成 QMT 可粘贴的规则字面量并同步策略内核副本；需人工导入沙箱。"},
    {"id": "tests", "group": "研究与演练", "label": "运行单元测试", "usage": "action", "target": "tests",
     "layer": "本地", "desc": "执行完整回归测试套件，验证内核、风控、实盘链路与 3.6 兼容。"},

    {"id": "qmt", "group": "执行层", "label": "QMT 执行层状态", "usage": "view", "target": "qmt",
     "layer": "QMT", "desc": "查看实时链路组成、六道闸门当前状态与人工导入步骤；实时日志在 QMT 客户端。"},
    {"id": "safety", "group": "执行层", "label": "安全状态", "usage": "view", "target": "safety",
     "layer": "两层", "desc": "只读、绑定地址、凭据、内核哈希一致性与实盘开关画像。"},

    {"id": "indicators", "group": "文档与合规", "label": "指标字典与规则 DSL", "usage": "doc", "target": "INDICATORS",
     "layer": "文档", "desc": "全部指标字段定义、计算口径与规则文件写法。"},
    {"id": "checklist", "group": "文档与合规", "label": "实盘上线检查表", "usage": "doc", "target": "GO_LIVE_CHECKLIST",
     "layer": "文档", "desc": "阶段 0~5 的验收门槛与剩余阻塞项。"},
    {"id": "compliance", "group": "文档与合规", "label": "合规边界", "usage": "doc", "target": "COMPLIANCE",
     "layer": "文档", "desc": "为什么实时决策必须闭环在券商沙箱内、哪些通道被禁止。"},
    {"id": "qmt-readme", "group": "文档与合规", "label": "QMT 接入说明", "usage": "doc", "target": "QMT_README",
     "layer": "文档", "desc": "券商 API 适配、probe 诊断与启用全自动的五步开关。"},
    {"id": "runbook", "group": "文档与合规", "label": "运维手册", "usage": "doc", "target": "RUNBOOK",
     "layer": "文档", "desc": "盘前 / 盘中 / 盘后流程与变更规范。"},
    {"id": "emergency", "group": "文档与合规", "label": "应急手册", "usage": "doc", "target": "EMERGENCY",
     "layer": "文档", "desc": "Kill Switch、撤单、熔断与异常处置顺序。"},
    {"id": "handoff", "group": "文档与合规", "label": "项目交接卡", "usage": "doc", "target": "PROJECT_CONTEXT",
     "layer": "文档", "desc": "压缩上下文；新会话继续提需求时先读它。"},
    {"id": "readme", "group": "文档与合规", "label": "README", "usage": "doc", "target": "README",
     "layer": "文档", "desc": "项目总览、快速开始与目录结构。"},

    {"id": "actions-result", "group": "系统", "label": "运行结果", "usage": "view", "target": "actions",
     "layer": "本地", "desc": "最近一次本地命令的退出码、耗时与完整输出。"},
    {"id": "action-log", "group": "系统", "label": "操作日志", "usage": "view", "target": "log",
     "layer": "本地", "desc": "本次服务进程内执行过的功能记录（仅内存，重启清空）。"},
]

# 只允许这 6 个固定命令；不接受任何用户参数，不经过 shell。
ACTION_NAMES = ("demo", "backtest", "scan", "export", "qmt-sim", "tests")

# 文档白名单：路径以项目根为基准，禁止任意路径。
DOC_FILES = {
    "INDICATORS": ("docs", "INDICATORS.md"),
    "GO_LIVE_CHECKLIST": ("docs", "GO_LIVE_CHECKLIST.md"),
    "COMPLIANCE": ("docs", "COMPLIANCE.md"),
    "RUNBOOK": ("ops", "RUNBOOK.md"),
    "EMERGENCY": ("ops", "EMERGENCY.md"),
    "QMT_README": ("execution", "qmt", "README.md"),
    "PROJECT_CONTEXT": ("PROJECT_CONTEXT.md",),
    "README": ("README.md",),
}

GATE_KEYS = ("BROKER_NAME", "ACCOUNT_ID", "KILL_SWITCH", "DRY_RUN", "MODE",
             "REPORT_CONFIRMED", "LIVE_ACKNOWLEDGED", "SCAN_PERIOD")


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


def features_payload():
    """左侧导航与功能梳理的数据源。"""
    return {
        "groups": list(GROUPS),
        "features": [dict(item) for item in FEATURES],
        "actions": list(ACTION_NAMES),
        "docs": sorted(DOC_FILES.keys()),
        "note": "所有 action 均为本地研究命令；本服务不提供下单、账户或实时桥接接口",
    }


def _read_gates():
    """用 AST 静态读取执行层开关，不导入、不执行 QMT 侧任何模块。"""
    path = os.path.join(_ROOT, "execution", "qmt", "live_config.py")
    gates = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                key = node.targets[0].id
                if key in GATE_KEYS:
                    try:
                        gates[key] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    except (IOError, OSError, SyntaxError):
        pass
    return gates


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def status_payload():
    """系统与合规状态画像（全部只读）。"""
    gates = _read_gates()
    core = os.path.join(_ROOT, "shared", "strategy_core.py")
    copy = os.path.join(_ROOT, "execution", "qmt", "strategy_core.py")
    snapshot = os.path.join(_ROOT, "execution", "qmt", "rules_snapshot.py")
    core_hash = _sha256(core) if os.path.isfile(core) else ""
    kernel_identical = bool(core_hash) and os.path.isfile(copy) and _sha256(copy) == core_hash
    snapshot_ok = False
    if os.path.isfile(snapshot) and core_hash:
        with open(snapshot, "r", encoding="utf-8") as handle:
            snapshot_ok = core_hash in handle.read()
    allowed = bool(
        gates.get("MODE") == "full_auto"
        and not gates.get("DRY_RUN", True)
        and not gates.get("KILL_SWITCH", True)
        and gates.get("REPORT_CONFIRMED") is True
        and gates.get("LIVE_ACKNOWLEDGED") is True
    )
    return {
        "bind": "127.0.0.1",
        "read_only": True,
        "broker": gates.get("BROKER_NAME", "未知"),
        "gates": gates,
        "live_order_allowed": allowed,
        "account_id_in_code": bool(gates.get("ACCOUNT_ID")),
        "rules_digest": rules_engine.rule_digest(rules_engine.load_rules()),
        "kernel_identical": kernel_identical,
        "snapshot_hash_matches": snapshot_ok,
        "demo_csv_exists": os.path.isfile(_csv_path()),
        "python": sys.version.split()[0],
        "action_whitelist": list(ACTION_NAMES),
        "no_realtime_bridge": True,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def read_doc(name):
    """读取白名单文档；拒绝未知名称与任何路径形式。"""
    if name not in DOC_FILES:
        raise KeyError("未授权的文档: %s" % name)
    full = os.path.abspath(os.path.join(_ROOT, *DOC_FILES[name]))
    if not full.startswith(os.path.abspath(_ROOT)) or not os.path.isfile(full):
        raise IOError("文档不存在")
    with open(full, "r", encoding="utf-8") as handle:
        text = handle.read(MAX_DOC_CHARS)
    return {"name": name, "path": full, "content": text, "chars": len(text)}


def _argv_for(name):
    if name == "tests":
        return [sys.executable, "-m", "unittest", "discover", "-s",
                os.path.join(_ROOT, "research", "tests")]
    return [sys.executable, os.path.join(_ROOT, "research", "cli.py"), name]


def run_action(name):
    """执行固定白名单里的本地研究命令。无参数、无 shell、无外网、串行、有超时。"""
    if name not in ACTION_NAMES:
        raise KeyError("未授权的动作: %s" % name)
    argv = _argv_for(name)
    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    # tests 动作会跑完整 unittest 套件；给子进程打标记，避免套件里
    # 的「调用 tests 动作」用例再次派生子进程，形成无限递归。
    env["CLAW_ACTION_CHILD"] = "1"
    started = time.time()
    with ACTION_LOCK:
        try:
            proc = subprocess.run(argv, cwd=_ROOT, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=ACTION_TIMEOUT)
            output = proc.stdout.decode("utf-8", "replace")
            code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            output = (exc.output or b"").decode("utf-8", "replace")
            output += "\n[claw] 命令超过 %s 秒，已终止" % ACTION_TIMEOUT
            code = -1
        except Exception as exc:
            output = "[claw] 命令启动失败: %s" % exc
            code = -2
        duration_ms = int((time.time() - started) * 1000)
        truncated = len(output) > MAX_OUTPUT_CHARS
        if truncated:
            output = output[:MAX_OUTPUT_CHARS] + "\n…（输出已截断）"
        result = {
            "ok": code == 0,
            "name": name,
            "exit_code": code,
            "output": output,
            "duration_ms": duration_ms,
            "truncated": truncated,
            "command": " ".join(argv),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        ACTION_LOG.append({
            "ts": result["ts"], "name": name, "ok": result["ok"],
            "exit_code": code, "duration_ms": duration_ms,
            "preview": output[:200],
        })
        del ACTION_LOG[:-50]
        return result


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
    server_version = "ClawLocal/2.0"

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

    def _json(self, status, payload):
        return self._send(status, json.dumps(payload, ensure_ascii=False),
                          "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            return self._json(200, {"ok": True, "bind": self.server.server_address[0]})
        if path == "/api/dashboard":
            try:
                return self._json(200, build_dashboard())
            except Exception as exc:
                return self._json(500, {"error": str(exc)})
        if path == "/api/rules":
            try:
                return self._json(200, rules_payload())
            except Exception as exc:
                return self._json(500, {"error": str(exc)})
        if path == "/api/capabilities":
            return self._json(200, {"capabilities": CAPABILITIES})
        if path == "/api/features":
            return self._json(200, features_payload())
        if path == "/api/status":
            try:
                return self._json(200, status_payload())
            except Exception as exc:
                return self._json(500, {"error": str(exc)})
        if path == "/api/action-log":
            return self._json(200, {"items": list(reversed(ACTION_LOG))})
        if path == "/api/docs":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            try:
                return self._json(200, read_doc(name))
            except (KeyError, IOError, OSError) as exc:
                return self._json(400, {"error": str(exc)})
        name = "index.html" if path in ("/", "") else os.path.basename(path)
        full = os.path.abspath(os.path.join(WEB_DIR, name))
        if not full.startswith(os.path.abspath(WEB_DIR)) or not os.path.isfile(full):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as handle:
            return self._send(200, handle.read(), CONTENT_TYPES.get(ext, "application/octet-stream"))

    def _read_body(self, limit):
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except (TypeError, ValueError):
            return None
        if length <= 0 or length > limit:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/rules":
            payload = self._read_body(MAX_RULES_BYTES)
            if payload is None:
                return self._json(400, {"error": "请求体无效或过大"})
            try:
                return self._json(200, save_rules(payload))
            except Exception as exc:
                return self._json(400, {"error": str(exc)})
        if path == "/api/actions":
            payload = self._read_body(MAX_ACTION_BYTES)
            if not isinstance(payload, dict):
                return self._json(400, {"error": "请求体必须是 {\"name\": ...}"})
            try:
                return self._json(200, run_action(payload.get("name")))
            except (KeyError, TypeError) as exc:
                return self._json(400, {"error": str(exc)})
        return self._send(405, "method not allowed", "text/plain; charset=utf-8")


def serve(host="127.0.0.1", port=8765):
    if host not in LOOPBACK:
        raise ValueError("refusing to bind non-loopback address: %s" % host)
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    server.daemon_threads = True
    sys.stderr.write("[claw] local workbench at http://%s:%s/ (read-only, no broker)\n"
                     % (host, port))
    return server


def start_background(host="127.0.0.1", port=8765):
    server = serve(host, port)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server
